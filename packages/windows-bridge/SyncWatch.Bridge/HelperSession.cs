using System.Diagnostics;
using System.Globalization;
using System.Text.Json;
using SyncWatch.Domain;

namespace SyncWatch.Bridge;

public interface IHelperTransport : IAsyncDisposable
{
    Task SendAsync(ReadOnlyMemory<byte> frame, CancellationToken cancellation = default);
    Task<byte[]> ReadAsync(CancellationToken cancellation = default);
}
public sealed record SharedMedia(string Handle, string Name, long DurationMs, ulong FileSizeBytes);
public sealed record SyncplayEndpoint(string Host, int Port, string Room, string Username);
public sealed record SyncplayParticipant(string Username, bool Ready);
public enum HelperConnection { Starting, Connecting, Reconnecting, Connected, Failed, Stopped }
public sealed record HelperSessionState(HelperConnection Connection = HelperConnection.Starting, bool Ready = false,
    string? AcceptedUsername = null, IReadOnlyList<SyncplayParticipant>? Participants = null, bool IdentityRejected = false, bool PlaybackReady = false);

/// <summary>One context owns native intents and one writer. Admit matching media before construction.</summary>
public sealed class HelperSession : IAsyncDisposable
{
    public HelperSessionState State { get; private set; } = new(Participants: Array.Empty<SyncplayParticipant>());
    public PlaybackScope WireScope { get; } = PlaybackScope.Create().ReplacingMedia();
    private readonly IHelperTransport transport;
    private readonly IPlaybackPort player;
    private readonly PlaybackScope nativeScope;
    private readonly SyncplayEndpoint endpoint;
    private readonly bool requireDeclaredIdentity;
    private readonly Func<CancellationToken, Task<SyncplayAdmission>>? admissionProvider;
    private readonly List<string> admissionNonces = [];
    private string? pendingAdmissionNonce;
    private readonly SharedMedia media;
    private readonly string revision;
    private readonly Action<HelperSessionState> changed;
    private readonly SynchronizationContext owner;
    private readonly int ownerThread = Environment.CurrentManagedThreadId;
    private readonly PlaybackActionFence fence;
    private readonly HelperCommands commands;
    private enum IntentKind { Pause, Toggle, Seek, PlayFrom }
    private sealed record Intent(ulong Sequence, IntentKind Kind, bool Paused = true, long PositionMs = 0);
    private readonly Queue<Intent> users = new();
    private readonly Queue<byte[]> inbox = new();
    private readonly List<PlayerCorrection> corrections = [];
    private readonly List<(PlayerCorrection Command, byte[] Data)> completed = [];
    private readonly List<(ulong Id, HelperReplyKind Expected, HelperReply Reply)> replies = [];
    private readonly CancellationTokenSource workCancellation = new(), readCancellation = new(), timerCancellation = new();
    private TaskCompletionSource? wake;
    private Task? reader, worker, timer, termination;
    private CancellationTokenSource? applying;
    private ulong commandSequence, correctionHigh, eventSequence, observationHigh;
    private bool heartbeat = true, observed, readyRequested, started, closed;
    private bool requestingReady, readinessObserved, recoveryIdentity, recoveryConnected;
    private long? deadline;
    private long? recoveryDeadline;

    public HelperSession(IHelperTransport transport, IPlaybackPort player, PlaybackScope nativeScope,
        SyncplayEndpoint endpoint, SharedMedia media, string revision, Action<HelperSessionState>? changed = null, bool requireDeclaredIdentity = false, Func<CancellationToken, Task<SyncplayAdmission>>? admissionProvider = null)
    {
        owner = SynchronizationContext.Current ?? throw new InvalidOperationException("An owning synchronization context is required");
        this.transport = transport; this.player = player; this.nativeScope = nativeScope; this.endpoint = endpoint;
        this.requireDeclaredIdentity = requireDeclaredIdentity; this.admissionProvider = admissionProvider;
        this.media = media; this.revision = revision; this.changed = changed ?? (_ => { });
        commands = new(WireScope); fence = new(nativeScope);
        _ = commands.Connect(1, endpoint.Host, endpoint.Port, endpoint.Room, endpoint.Username, accountAdmission: admissionProvider is not null);
        _ = commands.Loaded(1, media.Handle, media.Name, media.DurationMs, media.FileSizeBytes);
        if (media.DurationMs <= 0 || revision.Length != 40 || revision.Any(c => !char.IsAsciiHexDigitLower(c))) throw Bad();
    }
    public void Start()
    {
        CheckOwner(); if (started || closed) throw Bad(); started = true;
        reader = ReadLoopAsync(); timer = TimerAsync(); worker = RunGuardedAsync();
    }
    public void Pause(bool value) => Enqueue(IntentKind.Pause, paused: value);
    public void TogglePause() => Enqueue(IntentKind.Toggle);
    public void Seek(long positionMs)
    {
        if (positionMs < 0 || positionMs > media.DurationMs) throw Bad();
        Enqueue(IntentKind.Seek, position: positionMs);
    }
    public void PlayFrom(long positionMs)
    {
        if (positionMs < 0 || positionMs > media.DurationMs) throw Bad();
        Enqueue(IntentKind.PlayFrom, paused: false, position: positionMs);
    }
    private void Enqueue(IntentKind kind, bool paused = true, long position = 0)
    {
        CheckOwner(); if (closed || State.Connection != HelperConnection.Connected) throw Bad();
        if (users.Count >= 64) throw new IOException("queueFull");
        users.Enqueue(new(fence.BeginUserAction(nativeScope), kind, paused, position)); applying?.Cancel(); Signal();
    }
    private void CheckOwner() { if (Environment.CurrentManagedThreadId != ownerThread) throw new InvalidOperationException("Wrong session owner"); }
    private static IOException Bad() => new("unavailable");
    private void Signal() { var current = wake; wake = null; current?.TrySetResult(); }
    private void CheckOpen() { workCancellation.Token.ThrowIfCancellationRequested(); if (closed) throw Bad(); }
    private void SetDeadline(int seconds) => deadline = Stopwatch.GetTimestamp() + Stopwatch.Frequency * seconds;
    private ulong NextId() { if (commandSequence == PlaybackScope.MaximumGeneration) throw Bad(); return ++commandSequence; }
    private void Publish(HelperSessionState value) { State = value; changed(State); }
    private async Task ReadLoopAsync()
    {
        try
        {
            while (!closed)
            {
                byte[] frame = await transport.ReadAsync(readCancellation.Token);
                if (closed) return;
                if (inbox.Count >= 64) throw new IOException("queueFull");
                inbox.Enqueue(frame); Signal();
            }
        }
        catch (Exception) { if (!closed) Stop(true); }
    }
    private async Task TimerAsync()
    {
        try
        {
            while (!closed)
            {
                if ((deadline is long end && Stopwatch.GetTimestamp() >= end) ||
                    (recoveryDeadline is long recoveryEnd && Stopwatch.GetTimestamp() >= recoveryEnd)) { Stop(true); return; }
                heartbeat = true; Signal(); await Task.Delay(100, timerCancellation.Token);
            }
        }
        catch (OperationCanceledException) { }
    }
    private async Task RunGuardedAsync()
    {
        try { await RunAsync(); } catch (Exception) { if (!closed) Stop(true); }
    }
    private Task WaitSignalAsync()
    {
        CheckOpen(); wake = new(TaskCreationOptions.RunContinuationsAsynchronously); return wake.Task;
    }
    private async Task SendAsync(byte[] data)
    {
        CheckOpen(); SetDeadline(5); await transport.SendAsync(data, workCancellation.Token); deadline = null; CheckOpen();
    }
    private async Task RequestAsync(HelperReplyKind expected, Func<ulong, byte[]> build)
    {
        ulong id = NextId(); await SendAsync(build(id));
        long expires = Stopwatch.GetTimestamp() + Stopwatch.Frequency * 5;
        try
        {
            while (true)
            {
                CheckOpen(); deadline = expires;
                if (inbox.Count == 0) { await WaitSignalAsync(); continue; }
                byte[] data = inbox.Dequeue(); using var doc = PlayerCorrection.DecodeFrame(data); var value = doc.RootElement;
                if (value.TryGetProperty("replyTo", out var replyTo) && replyTo.ValueKind == JsonValueKind.String && replyTo.GetString() == "cmd-" + id)
                {
                    var reply = HelperReply.Decode(data, WireScope, id, expected, revision, requireAdmission: admissionProvider is not null);
                    if (reply.ErrorCode is not null) throw Bad();
                    replies.Add((id, expected, reply)); if (replies.Count > 128) replies.RemoveAt(0); return;
                }
                await ReceiveAsync(data, value);
            }
        }
        finally { deadline = null; }
    }
    private async Task RunAsync()
    {
        SetDeadline(2);
        Validate(await player.ObserveAsync(nativeScope, workCancellation.Token));
        SetDeadline(2); await player.SetPausedAsync(true, nativeScope, workCancellation.Token);
        while (!(await player.ObserveAsync(nativeScope, workCancellation.Token)).Paused) await Task.Delay(20, workCancellation.Token);
        deadline = null;
        await RequestAsync(HelperReplyKind.Ready, commands.Hello);
        await RequestAsync(HelperReplyKind.Applied, id => commands.Loaded(id, media.Handle, media.Name, media.DurationMs, media.FileSizeBytes));
        Publish(State with { Connection = HelperConnection.Connecting });
        await RequestAsync(HelperReplyKind.Applied, id => commands.Connect(id, endpoint.Host, endpoint.Port, endpoint.Room, endpoint.Username, accountAdmission: admissionProvider is not null));
        while (!closed)
        {
            CheckOpen();
            while (inbox.Count > 0)
            {
                byte[] data = inbox.Dequeue(); using var doc = PlayerCorrection.DecodeFrame(data); await ReceiveAsync(data, doc.RootElement);
            }
            if (pendingAdmissionNonce is string nonce && admissionProvider is not null)
            {
                // Credential preparation shares the authenticated transport's ten-second admission window.
                pendingAdmissionNonce = null; SetDeadline(10);
                var credential = await admissionProvider(workCancellation.Token); CheckOpen(); deadline = null;
                await RequestAsync(HelperReplyKind.Applied, id => commands.Admit(id, nonce, credential)); continue;
            }
            await CancelSupersededAsync();
            if (users.Count > 0) { await UserAsync(users.Dequeue()); continue; }
            if (corrections.Count > 0 && fence.Correction(nativeScope, corrections[0].AfterUserAction) == CorrectionDisposition.Apply)
            { await CorrectAsync(corrections[0]); continue; }
            if (heartbeat)
            {
                heartbeat = false; SetDeadline(2); var value = await player.ObserveAsync(nativeScope, workCancellation.Token); deadline = null; Validate(value);
                // VLC sequences identify events, not reads. Fresh paused samples
                // must reach Syncplay after login even with an unchanged event sequence.
                if (value.Sequence >= observationHigh)
                { await SendAsync(commands.Observation(NextId(), value, nativeScope)); observationHigh = value.Sequence; observed = true; }
            }
            if ((State.Connection == HelperConnection.Connected || recoveryConnected) && observed && !readyRequested)
            {
                requestingReady = true;
                await RequestAsync(HelperReplyKind.Applied, id => commands.Ready(id, true)); readyRequested = true;
                requestingReady = false; FinishReadiness(); continue;
            }
            if (inbox.Count == 0 && users.Count == 0 && !heartbeat) await WaitSignalAsync();
        }
    }
    private async Task ReconnectAsync()
    {
        // Do not replay unpublished intents or old timing corrections across transports.
        if (users.Count > 0 || fence.IssuedSequence != fence.PublishedSequence ||
            corrections.Any(c => c.Kind != CorrectionKind.Pause || !c.Paused)) throw Bad();
        recoveryDeadline ??= Stopwatch.GetTimestamp() + Stopwatch.Frequency * 25;
        recoveryIdentity = false; recoveryConnected = false; observed = false;
        readyRequested = false; requestingReady = false; readinessObserved = false;
        Publish(State with { Connection = HelperConnection.Reconnecting, Ready = false, PlaybackReady = false, Participants = Array.Empty<SyncplayParticipant>() });
        SetDeadline(2);
        try
        {
            await player.SetPausedAsync(true, nativeScope, workCancellation.Token);
            while (true)
            {
                var value = await player.ObserveAsync(nativeScope, workCancellation.Token); Validate(value);
                if (value.Paused) break;
                await Task.Delay(20, workCancellation.Token);
            }
        }
        finally { deadline = null; }
        heartbeat = true;
    }
    private void FinishReadiness()
    {
        if (!observed || !readyRequested || !(readinessObserved || State.Ready)) return;
        if (recoveryDeadline is not null)
        {
            if (!recoveryConnected) return;
            recoveryDeadline = null; recoveryConnected = false;
            State = State with { Connection = HelperConnection.Connected };
        }
        if (State.Connection != HelperConnection.Connected) return;
        Publish(State with { PlaybackReady = true });
    }
    private async Task UserAsync(Intent intent)
    {
        SetDeadline(2);
        bool? pause = intent.Kind is IntentKind.Pause or IntentKind.PlayFrom ? intent.Paused : intent.Kind == IntentKind.Toggle ? !(await player.ObserveAsync(nativeScope, workCancellation.Token)).Paused : null;
        long? position = intent.Kind is IntentKind.Seek or IntentKind.PlayFrom ? intent.PositionMs : null;
        if (position is long target) await player.SeekAsync(target, nativeScope, workCancellation.Token);
        if (pause is bool value) await player.SetPausedAsync(value, nativeScope, workCancellation.Token);
        SetDeadline(2);
        try
        {
            while (true)
            {
                CheckOpen(); var actual = await player.ObserveAsync(nativeScope, workCancellation.Token); Validate(actual);
                if ((pause is null || pause == actual.Paused) && (position is null || Math.Abs(position.Value - actual.PositionMs) <= 250))
                {
                    if (!fence.CanPublish(intent.Sequence, nativeScope)) return;
                    await SendAsync(commands.UserObservation(NextId(), intent.Sequence, actual, nativeScope));
                    fence.DidPublish(intent.Sequence, actual); observationHigh = actual.Sequence; return;
                }
                await Task.Delay(20, workCancellation.Token);
            }
        }
        finally { deadline = null; }
    }
    private async Task CorrectAsync(PlayerCorrection command)
    {
        using var cancellation = CancellationTokenSource.CreateLinkedTokenSource(workCancellation.Token);
        applying = cancellation; SetDeadline(2);
        try
        {
            var actual = await command.ApplyAsync(player, nativeScope, media.Handle, cancellation.Token); CheckOpen();
            if (fence.Correction(nativeScope, command.AfterUserAction) == CorrectionDisposition.Apply)
            { byte[] data = command.Result(actual, nativeScope); await SendAsync(data); observationHigh = Math.Max(observationHigh, actual.Sequence); Complete(command, data); }
        }
        catch (OperationCanceledException) { if (fence.IssuedSequence <= command.AfterUserAction) throw; }
        finally { applying = null; deadline = null; }
    }
    private async Task CancelSupersededAsync()
    {
        foreach (var command in corrections.Where(c => c.AfterUserAction < fence.PublishedSequence).ToArray())
        { byte[] data = command.CancelledReply(); await SendAsync(data); Complete(command, data); }
    }
    private void Complete(PlayerCorrection command, byte[] data)
    {
        corrections.RemoveAll(c => c.Id == command.Id); completed.Add((command, data));
        if (completed.Count > 128) completed.RemoveAt(0);
    }
    private async Task ReceiveAsync(byte[] data, JsonElement value)
    {
        string kind = PlayerCorrection.Text(value, "type");
        if (value.TryGetProperty("replyTo", out var replyTo))
        {
            string? id = replyTo.ValueKind == JsonValueKind.String ? replyTo.GetString() : null;
            var record = replies.FirstOrDefault(r => id == "cmd-" + r.Id);
            if (record.Id == 0 || HelperReply.Decode(data, WireScope, record.Id, record.Expected, revision, requireAdmission: admissionProvider is not null) != record.Reply) throw Bad();
            return;
        }
        if (kind.StartsWith("player.", StringComparison.Ordinal))
        {
            var command = PlayerCorrection.Decode(data, WireScope);
            var record = completed.FirstOrDefault(c => c.Command.Id == command.Id);
            if (record.Command is not null)
            { if (!record.Command.SameRequest(command)) throw Bad(); await SendAsync(record.Data); return; }
            var pending = corrections.FirstOrDefault(c => c.Id == command.Id);
            if (pending is not null) { if (!pending.SameRequest(command)) throw Bad(); return; }
            if (!ulong.TryParse(command.Id.AsSpan(4), NumberStyles.None, CultureInfo.InvariantCulture, out ulong number) || number <= correctionHigh || command.AfterUserAction > fence.IssuedSequence || corrections.Count >= 64) throw Bad();
            correctionHigh = number; corrections.Add(command); return;
        }
        PlayerCorrection.Keys(value, ["v", "id", "type", "clientRunId", "mediaGeneration", "payload"]);
        string eventId = PlayerCorrection.Text(value, "id");
        if (PlayerCorrection.Integer(value.GetProperty("v"), 1) != 1 || !eventId.StartsWith("event-", StringComparison.Ordinal)
            || !ulong.TryParse(eventId.AsSpan(6), NumberStyles.None, CultureInfo.InvariantCulture, out ulong ordinal) || eventId != "event-" + ordinal || ordinal != eventSequence + 1) throw Bad();
        WireScope.Check(PlayerCorrection.Text(value, "clientRunId"), PlayerCorrection.Integer(value.GetProperty("mediaGeneration"), PlaybackScope.MaximumGeneration));
        eventSequence = ordinal; var payload = value.GetProperty("payload");
        switch (kind)
        {
            case "sync.admissionRequired":
                PlayerCorrection.Keys(payload, ["nonce"]);
                string nonce = Text(payload.GetProperty("nonce"), 32);
                if (admissionProvider is null || pendingAdmissionNonce is not null || State.Connection is not (HelperConnection.Connecting or HelperConnection.Reconnecting)
                    || nonce.Length != 32 || nonce.Any(c => !char.IsAsciiHexDigitLower(c)) || admissionNonces.Contains(nonce)) throw Bad();
                if (admissionNonces.Count == 128) admissionNonces.RemoveAt(0);
                admissionNonces.Add(nonce); pendingAdmissionNonce = nonce;
                break;
            case "sync.identity":
                PlayerCorrection.Keys(payload, ["username"]); string username = Text(payload.GetProperty("username"), 64);
                if (requireDeclaredIdentity && username != endpoint.Username) { State = State with { IdentityRejected = true }; throw Bad(); }
                if (State.AcceptedUsername is not null && State.AcceptedUsername != username) { State = State with { IdentityRejected = true }; throw Bad(); }
                State = State with { AcceptedUsername = username };
                if (recoveryDeadline is not null) recoveryIdentity = true;
                break;
            case "sync.connection":
                PlayerCorrection.Keys(payload, ["state"], ["error"]);
                if (payload.TryGetProperty("error", out var error))
                {
                    byte[] wrapped = JsonSerializer.SerializeToUtf8Bytes(new { v = 1, replyTo = "cmd-1", type = "bridge.error", clientRunId = WireScope.ClientRunId, mediaGeneration = WireScope.MediaGeneration, payload = error }).Concat(new byte[] { 10 }).ToArray();
                    _ = HelperReply.Decode(wrapped, WireScope, 1, HelperReplyKind.Applied);
                }
                switch (PlayerCorrection.Text(payload, "state"))
                {
                    case "connecting":
                        if (State.Connection is HelperConnection.Connected or HelperConnection.Reconnecting) await ReconnectAsync();
                        else State = State with { Connection = HelperConnection.Connecting };
                        break;
                    case "connected":
                        if (State.AcceptedUsername is null) throw Bad();
                        if (recoveryDeadline is not null) { if (!recoveryIdentity) throw Bad(); recoveryConnected = true; observed = false; heartbeat = true; }
                        else State = State with { Connection = HelperConnection.Connected };
                        break;
                    default: throw Bad();
                }
                break;
            case "sync.readiness":
                PlayerCorrection.Keys(payload, ["ready"]); State = State with { Ready = Boolean(payload.GetProperty("ready")) };
                if (State.Ready && observed && (requestingReady || readyRequested)) readinessObserved = true;
                if (recoveryDeadline is not null && State.Ready)
                {
                    if (!recoveryConnected || !observed || !(requestingReady || readyRequested)) throw Bad();
                }
                FinishReadiness();
                break;
            case "sync.participants":
                PlayerCorrection.Keys(payload, ["participants"]); var rows = payload.GetProperty("participants");
                if (rows.ValueKind != JsonValueKind.Array || rows.GetArrayLength() > 64) throw Bad();
                List<SyncplayParticipant> people = [];
                foreach (var row in rows.EnumerateArray()) { PlayerCorrection.Keys(row, ["username", "ready"]); people.Add(new(Text(row.GetProperty("username"), 64), Boolean(row.GetProperty("ready")))); }
                State = State with { Participants = people.AsReadOnly() }; break;
            default: throw Bad();
        }
        changed(State);
    }
    private void Validate(PlaybackObservation value)
    {
        value.Validate(); nativeScope.Check(value.Scope.ClientRunId, value.Scope.MediaGeneration);
        if (value.Status != PlaybackStatus.Ready || value.DurationMs != media.DurationMs || (!value.Seekable && !(value.Ended && value.Paused))) throw Bad();
    }
    private static bool Boolean(JsonElement value) => value.ValueKind is JsonValueKind.True or JsonValueKind.False ? value.GetBoolean() : throw Bad();
    private static string Text(JsonElement value, int maximum)
    {
        if (value.ValueKind != JsonValueKind.String) throw Bad(); string text = value.GetString()!;
        if (text.EnumerateRunes().Count() is var count && (count < 1 || count > maximum) || text.Any(c => c < 32 || c == 127)) throw Bad(); return text;
    }
    private void Stop(bool failed)
    {
        CheckOwner(); if (closed) return; closed = true; fence.Invalidate(); users.Clear(); inbox.Clear(); corrections.Clear();
        Publish(State with { Connection = failed ? HelperConnection.Failed : HelperConnection.Stopped, Ready = false, PlaybackReady = false });
        applying?.Cancel(); workCancellation.Cancel(); timerCancellation.Cancel(); Signal();
        termination = Task.Run(async () =>
        {
            try { await player.SetPausedAsync(true, nativeScope, CancellationToken.None).ConfigureAwait(false); } catch (Exception) { }
            await transport.DisposeAsync().ConfigureAwait(false); readCancellation.Cancel();
            if (worker is not null) await worker.ConfigureAwait(false); if (reader is not null) await reader.ConfigureAwait(false);
        });
    }
    public async ValueTask DisposeAsync()
    {
        if (Environment.CurrentManagedThreadId == ownerThread) { if (!closed) Stop(false); }
        else
        {
            var receipt = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            owner.Post(_ => { if (!closed) Stop(false); receipt.SetResult(); }, null); await receipt.Task.ConfigureAwait(false);
        }
        if (termination is not null) await termination.ConfigureAwait(false);
    }
}
