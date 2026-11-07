using System.Collections.Concurrent;
using System.Text.Json;
using System.Threading.Channels;
using SyncWatch.Bridge;
using SyncWatch.Domain;

internal static class HelperSessionChecks
{
    private const string Revision = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private static void Require(bool value) { if (!value) throw new InvalidDataException("Session invariant failed"); }
    private static async Task Eventually(Func<bool> predicate)
    {
        var end = DateTime.UtcNow.AddSeconds(3);
        while (!predicate()) { if (DateTime.UtcNow >= end) throw new TimeoutException("Session check"); await Task.Delay(10); }
    }

    private sealed class OwnerContext : SynchronizationContext
    {
        private readonly BlockingCollection<(SendOrPostCallback Callback, object? State)> queue = new();
        public override void Post(SendOrPostCallback d, object? state) => queue.Add((d, state));
        internal void Run(Func<Task> action)
        {
            var previous = Current; SetSynchronizationContext(this);
            try
            {
                Task task = action();
                _ = task.ContinueWith(_ => Post(_ => queue.CompleteAdding(), null), TaskScheduler.Default);
                foreach (var item in queue.GetConsumingEnumerable()) item.Callback(item.State);
                task.GetAwaiter().GetResult();
            }
            finally { SetSynchronizationContext(previous); queue.Dispose(); }
        }
    }

    // This peer scripts IPC ordering; it contains no synchronization decisions.
    private sealed class Peer : IHelperTransport
    {
        private readonly Channel<byte[]> incoming = Channel.CreateBounded<byte[]>(64);
        private readonly List<JsonElement> sent = [];
        private JsonElement context;
        private ulong eventSequence;
        private TaskCompletionSource? writing;
        private bool holdUser;
        internal bool StationaryHandshake { get; set; }
        private int samples;
        internal bool TicketMode { get; set; }
        internal bool AdvertiseAdmission { get; set; } = true;
        private bool automaticReady = true;
        internal bool? PulseBeforeAck { get; set; }
        internal bool UserWriteHeld { get; private set; }
        private Dictionary<string, object?> Frame(string kind, object payload) => new()
        {
            ["v"] = 1,
            ["type"] = kind,
            ["payload"] = payload,
            ["clientRunId"] = context.GetProperty("clientRunId").GetString(),
            ["mediaGeneration"] = context.GetProperty("mediaGeneration").GetUInt64()
        };
        private void Push(Dictionary<string, object?> value)
        {
            byte[] frame = JsonSerializer.SerializeToUtf8Bytes(value).Concat(new byte[] { 10 }).ToArray();
            Require(incoming.Writer.TryWrite(frame));
        }
        private void Event(string kind, object payload)
        {
            var frame = Frame(kind, payload); frame["id"] = "event-" + ++eventSequence; Push(frame);
        }
        public async Task SendAsync(ReadOnlyMemory<byte> frame, CancellationToken cancellation = default)
        {
            using var doc = JsonDocument.Parse(frame); var value = doc.RootElement; context = value.Clone();
            if (value.TryGetProperty("origin", out var origin) && origin.GetString() == "user" && holdUser)
            {
                UserWriteHeld = true; writing = new(TaskCreationOptions.RunContinuationsAsynchronously);
                await writing.Task.WaitAsync(cancellation);
            }
            cancellation.ThrowIfCancellationRequested(); sent.Add(value.Clone());
            if (!value.TryGetProperty("id", out var id)) return;
            string kind = value.GetProperty("type").GetString()!;
            if (kind == "player.state")
            {
                if (++samples == 2 && StationaryHandshake)
                { Event("sync.identity", new { username = "Viewer_2" }); Event("sync.connection", new { state = "connected" }); }
                return;
            }
            bool hello = kind == "bridge.hello";
            var reply = Frame(hello ? "bridge.ready" : "bridge.result",
                hello ? (AdvertiseAdmission ? new { syncplayRevision = Revision, admissionVersion = 1 } : (object)new { syncplayRevision = Revision }) : new { status = "applied" });
            if (kind == "sync.connect" && TicketMode)
            {
                Require(value.GetProperty("payload").GetProperty("accountAdmission").GetBoolean());
                Event("sync.admissionRequired", new { nonce = new string('a', 32) });
            }
            if (kind == "sync.setReady" && PulseBeforeAck == true)
            { Event("sync.readiness", new { ready = true }); Event("sync.readiness", new { ready = false }); }
            reply["replyTo"] = id.GetString(); Push(reply);
            if (kind == "sync.connect" && !TicketMode && !StationaryHandshake || kind == "sync.admit")
            { Event("sync.identity", new { username = "Viewer_2" }); Event("sync.connection", new { state = "connected" }); }
            if (kind == "sync.setReady" && automaticReady && PulseBeforeAck != true)
            { Event("sync.readiness", new { ready = true }); if (PulseBeforeAck == false) Event("sync.readiness", new { ready = false }); }
        }
        public Task<byte[]> ReadAsync(CancellationToken cancellation = default) => incoming.Reader.ReadAsync(cancellation).AsTask();
        public ValueTask DisposeAsync() { incoming.Writer.TryComplete(); writing?.TrySetCanceled(); return ValueTask.CompletedTask; }
        internal void RequestAdmission(string nonce) => Event("sync.admissionRequired", new { nonce });
        internal int AdmissionCount => sent.Count(v => v.GetProperty("type").GetString() == "sync.admit");
        internal bool AdmissionMatches(int index, string ticket, string nonce)
        {
            var payload = sent.Where(v => v.GetProperty("type").GetString() == "sync.admit").ElementAt(index).GetProperty("payload");
            return payload.GetProperty("ticket").GetString() == ticket && payload.GetProperty("nonce").GetString() == nonce && payload.GetProperty("clientRunId").GetString() == "member-run";
        }
        internal void Readiness(bool value) => Event("sync.readiness", new { ready = value });
        internal void Connection(string value) => Event("sync.connection", new { state = value });
        internal void Identity(string value) => Event("sync.identity", new { username = value });
        internal void HoldReadiness() => automaticReady = false;
        internal int ReadyCount => sent.Count(v => v.GetProperty("type").GetString() == "sync.setReady");
        internal void GateUserWrite() => holdUser = true;
        internal void ReleaseUserWrite() { holdUser = false; writing?.TrySetResult(); writing = null; }
        internal void Correction(int id, int after, long position)
        {
            var frame = Frame("player.setPosition", new { positionMs = position });
            frame["id"] = "cmd-" + id; frame["afterUserAction"] = after; Push(frame);
        }
        internal int ReplyCount(int id, string kind) => sent.Count(v => v.TryGetProperty("replyTo", out var reply) && reply.GetString() == "cmd-" + id && v.GetProperty("type").GetString() == kind);
        internal bool SawNativeEnd => sent.Any(v => v.GetProperty("type").GetString() == "player.state" && v.GetProperty("payload").GetProperty("positionMs").GetInt64() == 60000 && !v.GetProperty("payload").GetProperty("seekable").GetBoolean() && v.GetProperty("payload").GetProperty("paused").GetBoolean());
        internal bool UserMatches(long position, bool paused)
        {
            var frame = sent.Last(v => v.TryGetProperty("origin", out var origin) && origin.GetString() == "user");
            var payload = frame.GetProperty("payload");
            return payload.GetProperty("positionMs").GetInt64() == position && payload.GetProperty("paused").GetBoolean() == paused;
        }
        internal int UserCount => sent.Count(v => v.TryGetProperty("origin", out var origin) && origin.GetString() == "user");
    }

    private sealed class Player(PlaybackScope owner) : IPlaybackPort
    {
        private ulong sequence = 1;
        internal bool Paused { get; private set; }
        internal long Position { get; private set; } = 1200;
        internal int Seeks { get; private set; }
        internal int Closes { get; private set; }
        private bool ended;
        internal void FinishMedia() { sequence++; ended = true; Paused = true; Position = 60000; }
        public Task<PlaybackObservation> ObserveAsync(PlaybackScope scope, CancellationToken cancellation = default)
        {
            cancellation.ThrowIfCancellationRequested(); owner.Check(scope.ClientRunId, scope.MediaGeneration);
            return Task.FromResult(new PlaybackObservation
            {
                Scope = scope,
                Sequence = sequence,
                Status = PlaybackStatus.Ready,
                PositionMs = Position,
                DurationMs = 60000,
                Paused = Paused,
                Seekable = !ended,
                Ended = ended
            });
        }
        public Task SetPausedAsync(bool paused, PlaybackScope scope, CancellationToken cancellation = default)
        { cancellation.ThrowIfCancellationRequested(); if (Paused != paused) sequence++; Paused = paused; return Task.CompletedTask; }
        public Task SeekAsync(long positionMs, PlaybackScope scope, CancellationToken cancellation = default)
        { cancellation.ThrowIfCancellationRequested(); sequence++; Position = positionMs; Seeks++; return Task.CompletedTask; }
        public Task LoadAsync(string file, PlaybackScope scope, CancellationToken cancellation = default) => throw new InvalidOperationException();
        public Task SetRateAsync(double rate, PlaybackScope scope, CancellationToken cancellation = default) => throw new InvalidOperationException();
        public Task SetVolumeAsync(double volume, bool muted, PlaybackScope scope, CancellationToken cancellation = default) => throw new InvalidOperationException();
        public Task<IReadOnlyList<PlaybackTrack>> TracksAsync(bool subtitles, PlaybackScope scope, CancellationToken cancellation = default) => throw new InvalidOperationException();
        public Task SelectTrackAsync(int id, bool subtitles, PlaybackScope scope, CancellationToken cancellation = default) => throw new InvalidOperationException();
        public Task StopAsync(PlaybackScope scope, CancellationToken cancellation = default) => throw new InvalidOperationException();
        public ValueTask DisposeAsync() { Closes++; return ValueTask.CompletedTask; }
    }

    private static async Task Scenario(bool eof, bool strict = false, bool nativeEnd = false)
    {
        var native = PlaybackScope.Create().ReplacingMedia(); var player = new Player(native); var peer = new Peer();
        await using var session = new HelperSession(peer, player, native, new("localhost", 8999, "room", "Viewer"),
            new("media-1", "Movie.mkv", 60000, 1234), Revision, requireDeclaredIdentity: strict);
        if (strict)
        {
            await player.SetPausedAsync(false, native);
            session.Start(); await Eventually(() => session.State.Connection == HelperConnection.Failed);
            await session.DisposeAsync(); await session.DisposeAsync();
            Require(!session.State.Ready && session.State.IdentityRejected && session.State.AcceptedUsername is null && peer.UserCount == 0 && player.Paused && player.Closes == 0);
            return;
        }
        session.Start(); await Eventually(() => session.State.Ready);
        Require(session.State.AcceptedUsername == "Viewer_2" && player.Paused && peer.UserCount == 0);
        if (nativeEnd)
        {
            player.FinishMedia(); await Eventually(() => peer.SawNativeEnd);
            Require(session.State.Connection == HelperConnection.Connected && session.State.Ready && peer.UserCount == 0);
        }
        else if (eof)
        {
            session.Pause(false); await Eventually(() => peer.UserCount == 1);
            await peer.DisposeAsync(); await Eventually(() => session.State.Connection == HelperConnection.Failed);
        }
        else
        {
            peer.GateUserWrite(); session.Pause(false); await Eventually(() => peer.UserWriteHeld);
            peer.Correction(1, 1, 2400); await Task.Delay(30);
            Require(session.State.Connection == HelperConnection.Connected && player.Seeks == 0);
            peer.ReleaseUserWrite(); await Eventually(() => peer.ReplyCount(1, "player.result") == 1);
            peer.Correction(1, 1, 2400); await Eventually(() => peer.ReplyCount(1, "player.result") == 2);
            Require(player.Seeks == 1);
            peer.Readiness(false); await Eventually(() => !session.State.Ready);
            session.Seek(3000); await Eventually(() => peer.UserCount == 2);
            peer.Correction(2, 0, 0); await Eventually(() => peer.ReplyCount(2, "bridge.error") == 1);
            Require(player.Position == 3000 && peer.UserCount == 2);
            peer.Correction(3, 3, 0); await Eventually(() => session.State.Connection == HelperConnection.Failed);
        }
        await session.DisposeAsync(); await session.DisposeAsync(); Require(player.Paused && player.Closes == 0);
    }
    private static async Task Recovery(string scenario)
    {
        var native = PlaybackScope.Create().ReplacingMedia(); var player = new Player(native); var peer = new Peer();
        await using var session = new HelperSession(peer, player, native, new("localhost", 8999, "room", "Viewer_2"),
            new("media-1", "Movie.mkv", 60000, 1234), Revision, requireDeclaredIdentity: true);
        session.Start(); await Eventually(() => session.State.Ready); peer.HoldReadiness();
        if (scenario is "pendingIntent" or "pendingCorrection") peer.GateUserWrite();
        session.Pause(scenario == "paused");
        if (scenario is "pendingIntent" or "pendingCorrection")
        {
            await Eventually(() => peer.UserWriteHeld);
            if (scenario == "pendingIntent") session.Seek(4000); else peer.Correction(1, 1, 4000);
            peer.Connection("connecting"); await Task.Delay(30); peer.ReleaseUserWrite();
            await Eventually(() => session.State.Connection == HelperConnection.Failed); Require(peer.UserCount == 1);
        }
        else
        {
            await Eventually(() => peer.UserCount == 1); peer.Connection("connecting");
            await Eventually(() => session.State.Connection == HelperConnection.Reconnecting && !session.State.Ready);
            await Eventually(() => player.Paused); Require(session.State.AcceptedUsername == "Viewer_2");
            bool blocked = false; try { session.TogglePause(); } catch (IOException) { blocked = true; }
            Require(blocked);
            blocked = false; try { session.Seek(4000); } catch (IOException) { blocked = true; }
            Require(blocked);
            switch (scenario)
            {
                case "missingIdentity":
                    peer.Connection("connected"); await Eventually(() => session.State.Connection == HelperConnection.Failed); break;
                case "renamed":
                    peer.Identity("Viewer_3"); await Eventually(() => session.State.Connection == HelperConnection.Failed);
                    Require(session.State.IdentityRejected); break;
                case "earlyReady":
                    peer.Readiness(true); await Eventually(() => session.State.Connection == HelperConnection.Failed); break;
                case "cancel":
                    await session.DisposeAsync(); Require(session.State.Connection == HelperConnection.Stopped); break;
                case "timeout":
                    await Task.Delay(20000); peer.Connection("connecting"); await Task.Delay(5300);
                    await Eventually(() => session.State.Connection == HelperConnection.Failed); break;
                default:
                    for (int attempt = 2; attempt <= 3; attempt++)
                    {
                        peer.Identity("Viewer_2"); peer.Connection("connected"); await Eventually(() => peer.ReadyCount == attempt);
                        Require(session.State.Connection == HelperConnection.Reconnecting && !session.State.Ready);
                        peer.Readiness(true); await Eventually(() => session.State.Connection == HelperConnection.Connected && session.State.Ready);
                        session.Seek(attempt * 1000); await Eventually(() => peer.UserCount == attempt);
                        if (attempt == 2) { peer.Connection("connecting"); await Eventually(() => session.State.Connection == HelperConnection.Reconnecting); }
                    }
                    Require(player.Position == 3000); break;
            }
        }
        await session.DisposeAsync(); await session.DisposeAsync(); Require(player.Paused && player.Closes == 0);
    }
    private static async Task ReadinessPulse(bool beforeAck)
    {
        var native = PlaybackScope.Create().ReplacingMedia(); var player = new Player(native); var peer = new Peer { PulseBeforeAck = beforeAck };
        await using var session = new HelperSession(peer, player, native, new("localhost", 8999, "room", "Viewer"),
            new("media-1", "Movie.mkv", 60000, 1234), Revision);
        session.Start(); await Eventually(() => session.State.PlaybackReady && !session.State.Ready);
        Require(session.State.Connection == HelperConnection.Connected);
        foreach (long invalid in new[] { -1L, 60001L })
        { try { session.PlayFrom(invalid); throw new InvalidDataException("Invalid resume accepted"); } catch (IOException) { } }
        session.PlayFrom(22000); await Eventually(() => peer.UserCount == 1);
        Require(peer.UserMatches(22000, false) && player.Position == 22000 && !player.Paused && peer.ReadyCount == 1);
        await session.DisposeAsync(); Require(!session.State.PlaybackReady && player.Closes == 0);
    }
    private static async Task StationaryHandshake()
    {
        var native = PlaybackScope.Create().ReplacingMedia(); var player = new Player(native); var peer = new Peer { StationaryHandshake = true };
        await using var session = new HelperSession(peer, player, native, new("localhost", 8999, "room", "Viewer"),
            new("media-1", "Movie.mkv", 60000, 1234), Revision);
        session.Start(); await Eventually(() => session.State.PlaybackReady);
        Require(player.Paused && player.Position == 1200 && peer.UserCount == 0 && player.Closes == 0);
    }
    private static async Task AdmissionChecks()
    {
        var native = PlaybackScope.Create().ReplacingMedia(); var peer = new Peer { TicketMode = true }; int calls = 0;
        await using (var session = new HelperSession(peer, new Player(native), native, new("localhost", 8999, "room", "Viewer_2"),
            new("media-1", "Movie.mkv", 60000, 1234), Revision, requireDeclaredIdentity: true,
            admissionProvider: async token =>
            {
                calls++;
                if (calls == 2) await Task.Delay(TimeSpan.FromSeconds(6), token);
                return new SyncplayAdmission(new string(calls == 1 ? 'A' : 'B', 64), "member-run");
            }))
        {
            session.Start(); await Eventually(() => session.State.PlaybackReady);
            Require(calls == 1 && peer.AdmissionMatches(0, new string('A', 64), new string('a', 32)));
            peer.Connection("connecting"); peer.RequestAdmission(new string('b', 32));
            await Eventually(() => calls == 2);
            Require(session.State.Connection == HelperConnection.Reconnecting && !session.State.PlaybackReady);
            await Task.Delay(TimeSpan.FromSeconds(6));
            Require(session.State.Connection != HelperConnection.Failed);
            await Eventually(() => calls == 2 && session.State.PlaybackReady);
            Require(peer.AdmissionMatches(1, new string('B', 64), new string('b', 32)));
            peer.Connection("connecting"); peer.RequestAdmission(new string('a', 32));
            await Eventually(() => session.State.Connection == HelperConnection.Failed); Require(calls == 2);
        }
        foreach ((bool missing, bool expires) in new[] { (false, false), (true, false), (false, true) })
        {
            calls = 0; bool cancelled = false; peer = new Peer { TicketMode = true, AdvertiseAdmission = !missing };
            await using var session = new HelperSession(peer, new Player(native), native, new("localhost", 8999, "room", "Viewer_2"),
                new("media-1", "Movie.mkv", 60000, 1234), Revision, admissionProvider: async token =>
                {
                    calls++;
                    try { await Task.Delay(TimeSpan.FromSeconds(20), token); }
                    catch (OperationCanceledException) { cancelled = true; throw; }
                    return new(new string('A', 64), "member-run");
                });
            session.Start(); await Eventually(() => missing ? session.State.Connection == HelperConnection.Failed : calls == 1);
            if (expires)
            {
                await Task.Delay(TimeSpan.FromSeconds(11));
                Require(session.State.Connection == HelperConnection.Failed && cancelled);
            }
            await session.DisposeAsync(); await session.DisposeAsync();
            Require(calls == (missing ? 0 : 1) && (missing || cancelled) && peer.AdmissionCount == 0);
        }
        Require(new SyncplayAdmission(new string('A', 64), "member-run").ToString() == "[redacted]");
    }
    internal static void Run()
    {
        new OwnerContext().Run(async () =>
        {
            await AdmissionChecks();
            await StationaryHandshake();
            await ReadinessPulse(false); await ReadinessPulse(true);
            await Scenario(false); await Scenario(true); await Scenario(false, strict: true); await Scenario(false, nativeEnd: true);
            foreach (string scenario in new[] { "recover", "paused", "missingIdentity", "renamed", "earlyReady", "pendingIntent", "pendingCorrection", "cancel", "timeout" }) await Recovery(scenario);
        });
        Console.WriteLine("SYNCWATCH_HELPER_SESSION_OK publication race replay stale correction future authority EOF recovery identity readiness budget cleanup");
    }
}
