using System.Threading.Channels;

namespace SyncWatch.RoomMetadata;

public sealed record RoomLimits
{
    public int MaximumRooms { get; init; } = 16;
    public int MaximumMembers { get; init; } = 8;
    public int CatalogItems { get; init; } = 256;
    public int ReplayEvents { get; init; } = 256;
    public int ReplayBytes { get; init; } = 1_048_576;
    public int QueueFrames { get; init; } = 32;
    public int CommandsPerSecond { get; init; } = 32;
    public TimeSpan RoomLifetime { get; init; } = TimeSpan.FromHours(8);
    public TimeSpan InvitationLifetime { get; init; } = TimeSpan.FromMinutes(15);
    public TimeSpan DisconnectGrace { get; init; } = TimeSpan.FromSeconds(30);
    public TimeSpan CatalogSnapshotLifetime { get; init; } = TimeSpan.FromSeconds(60);

    public void Validate()
    {
        Wire.Need(MaximumRooms is >= 1 and <= 64 && MaximumMembers is >= 1 and <= 64
            && CatalogItems is >= 1 and <= 2048 && ReplayEvents is >= 1 and <= 1024
            && ReplayBytes is >= Wire.MaximumBytes and <= 16_777_216 && QueueFrames is >= 2 and <= 256
            && CommandsPerSecond is >= 1 and <= 256);
        foreach (TimeSpan value in new[] { RoomLifetime, InvitationLifetime, DisconnectGrace, CatalogSnapshotLifetime })
        {
            Wire.Need(value >= TimeSpan.FromSeconds(1) && value <= TimeSpan.FromDays(1));
        }
    }
}

public sealed class RoomStream(int capacity) : IDisposable
{
    public Channel<byte[]> Frames { get; } = Channel.CreateBounded<byte[]>(new BoundedChannelOptions(capacity)
    {
        SingleReader = true,
        FullMode = BoundedChannelFullMode.Wait
    });
    public CancellationTokenSource Stopped { get; } = new();

    public void Enqueue(object value)
    {
        byte[] bytes = System.Text.Json.JsonSerializer.SerializeToUtf8Bytes(value, Wire.Json);
        Wire.Need(bytes.Length <= Wire.MaximumBytes, "frameTooLarge", 413);
        if (!Frames.Writer.TryWrite(bytes)) { Stop(); }
    }

    public void Stop() { Stopped.Cancel(); Frames.Writer.TryComplete(); }
    public void Dispose() { Stop(); Stopped.Dispose(); }
}

/// <summary>One bounded in-memory authority. Mutations and stream attachment are serialized.</summary>
public sealed class RoomStore
{
    private sealed class Member(MemberBinding binding, byte[] token, bool owner, DateTimeOffset deadline)
    {
        public MemberBinding Binding { get; } = binding;
        public byte[] Token { get; } = token;
        public bool Owner { get; } = owner;
        public DateTimeOffset? DisconnectedDeadline { get; set; } = deadline;
        public RoomStream? Stream { get; set; }
        public MediaDescriptor[] Published { get; set; } = [];
        public string? PendingBatch { get; set; }
        public MediaDescriptor[] Pending { get; set; } = [];
        public bool Missing { get; set; }
        public long HighestCommand { get; set; }
        public Dictionary<long, (string Digest, CommandResult Result)> Results { get; } = [];
        public Queue<long> ResultOrder { get; } = new();
        public DateTimeOffset RateWindow { get; set; }
        public int RateCount { get; set; }
        public CatalogView? View { get; set; }
    }

    private sealed record CatalogView(string Id, DateTimeOffset ExpiresAt, RoomSnapshot Snapshot, CatalogEntry[] Items);
    private sealed class Room(string id, SyncplayBinding connection, DateTimeOffset expiresAt, byte[] invitation, DateTimeOffset invitationExpiry)
    {
        public string Id { get; } = id;
        public string Epoch { get; } = Wire.NewId("epoch-");
        public SyncplayBinding Connection { get; } = connection;
        public DateTimeOffset ExpiresAt { get; } = expiresAt;
        public byte[] Invitation { get; set; } = invitation;
        public DateTimeOffset InvitationExpiry { get; set; } = invitationExpiry;
        public Dictionary<string, Member> Members { get; } = new(StringComparer.Ordinal);
        public Queue<(RoomEvent Event, int Bytes)> Events { get; } = new();
        public int EventBytes { get; set; }
        public long Sequence { get; set; }
        public long Generation { get; set; }
        public WatchState Watch { get; set; } = new("idle", null);
        public CatalogEntry? Selection { get; set; }
        public string? MeetUri { get; set; }
    }

    private readonly object gate = new();
    private readonly Dictionary<string, Room> rooms = new(StringComparer.Ordinal);
    private readonly TimeProvider clock;
    private readonly RoomLimits limits;

    public RoomStore(RoomLimits? limits = null, TimeProvider? clock = null)
    {
        this.limits = limits ?? new();
        this.limits.Validate();
        this.clock = clock ?? TimeProvider.System;
    }

    public Admission Create(System.Text.Json.JsonElement request)
    {
        Wire.Fields(request, "v", "clientRunId", "syncplayUsername", "syncplayHost", "syncplayPort", "syncplayRoom");
        Wire.Version(request);
        string run = Wire.String(request, "clientRunId"), username = Wire.String(request, "syncplayUsername");
        string host = Wire.String(request, "syncplayHost"), name = Wire.String(request, "syncplayRoom");
        int port = (int)Wire.Integer(request, "syncplayPort", 65535);
        Wire.Need(Wire.Id(run) && Wire.Text(username, 64) && Wire.Text(host, 253)
            && Uri.CheckHostName(host) != UriHostNameType.Unknown && port > 0 && Wire.Text(name, 64));
        lock (gate)
        {
            SweepCore();
            Wire.Need(rooms.Count < limits.MaximumRooms, "queueFull", 429);
            string invitation = Wire.Secret();
            DateTimeOffset now = clock.GetUtcNow();
            Room room = new(Wire.NewId("room-"), new(host, port, name), now + limits.RoomLifetime,
                Wire.Hash(invitation), now + limits.InvitationLifetime);
            rooms.Add(room.Id, room);
            return AddMember(room, run, username, owner: true, invitation);
        }
    }

    public Admission Join(string roomId, System.Text.Json.JsonElement request)
    {
        Wire.Fields(request, "v", "invitation", "clientRunId", "syncplayUsername");
        Wire.Version(request);
        string run = Wire.String(request, "clientRunId"), username = Wire.String(request, "syncplayUsername");
        Wire.Need(Wire.Id(run) && Wire.Text(username, 64));
        lock (gate)
        {
            SweepCore();
            Room room = Find(roomId);
            Wire.Need(clock.GetUtcNow() < room.InvitationExpiry && Wire.SecretMatches(Wire.String(request, "invitation"), room.Invitation), "unauthorized", 401);
            Wire.Need(room.Members.Count < limits.MaximumMembers, "queueFull", 429);
            Wire.Need(!room.Members.Values.Any(member => member.Binding.SyncplayUsername == username), "conflict", 409);
            return AddMember(room, run, username, owner: false, null);
        }
    }

    // Called only by the account authority around its own private store.
    internal Admission Admit(string roomId, string run, string username)
    {
        lock (gate)
        {
            SweepCore(); Room room = Find(roomId);
            Wire.Need(Wire.Id(run) && Wire.Text(username, 64));
            Wire.Need(room.Members.Count < limits.MaximumMembers, "queueFull", 429);
            Wire.Need(!room.Members.Values.Any(member => member.Binding.SyncplayUsername == username), "conflict", 409);
            return AddMember(room, run, username, owner: false, null);
        }
    }

    private Admission AddMember(Room room, string run, string username, bool owner, string? invitation)
    {
        Wire.Need(room.Sequence < Wire.MaximumCounter, "unavailable", 503);
        string token = Wire.Secret();
        Member member = new(new(Wire.NewId("member-"), run, username), Wire.Hash(token), owner, clock.GetUtcNow() + limits.DisconnectGrace);
        room.Members.Add(member.Binding.MemberId, member);
        Publish(room, member, "member.bound", member.Binding);
        return new(room.Id, member.Binding.MemberId, token, room.ExpiresAt, BootstrapCore(room, member), invitation);
    }

    public Bootstrap Snapshot(string roomId, string token)
    {
        lock (gate) { (Room room, Member member) = Authorize(roomId, token); return BootstrapCore(room, member); }
    }

    // Authority checks must not create a new catalog view or invalidate a caller's cursor.
    internal bool Contains(string roomId, string epoch, MemberBinding binding, string token)
    {
        lock (gate)
        {
            SweepCore();
            return rooms.TryGetValue(roomId, out Room? room) && room.Epoch == epoch
                && room.Members.TryGetValue(binding.MemberId, out Member? member)
                && member.Binding == binding && Wire.SecretMatches(token, member.Token);
        }
    }

    private Bootstrap BootstrapCore(Room room, Member member)
    {
        RoomSnapshot snapshot = new(1, room.Epoch, room.Sequence, room.Generation, room.Watch,
            room.Members.Values.Select(item => item.Binding).ToArray(), room.MeetUri);
        member.View = new(Wire.NewId("catalog-"), clock.GetUtcNow() + limits.CatalogSnapshotLifetime, snapshot,
            room.Members.Values.SelectMany(item => item.Published.Select(media => new CatalogEntry(item.Binding.MemberId, media))).ToArray());
        return new(snapshot, room.Connection, member.View.Id + ":0", room.Members.Values.Where(item => item.Missing).Select(item => item.Binding.MemberId).ToArray());
    }

    // Selection owns the descriptor accepted at selection.changed, independently of later catalogs.
    // Reading it neither creates nor invalidates a membership's stable catalog cursor.
    public SelectionDefinition Selection(string roomId, string token, long generation)
    {
        lock (gate)
        {
            (Room room, _) = Authorize(roomId, token);
            Wire.Need(generation is >= 0 and <= Wire.MaximumCounter);
            Wire.Need(generation == room.Generation, "staleGeneration", 409);
            return new(1, room.Epoch, room.Sequence, room.Generation, room.Selection);
        }
    }

    public CatalogPage Catalog(string roomId, string token, string cursor)
    {
        lock (gate)
        {
            (Room room, Member member) = Authorize(roomId, token);
            string[] parts = cursor.Split(':');
            Wire.Need(parts.Length == 2 && int.TryParse(parts[1], out _) && member.View is not null, "snapshotRequired", 409);
            CatalogView view = member.View!;
            int offset = int.Parse(parts[1], System.Globalization.CultureInfo.InvariantCulture);
            Wire.Need(parts[0] == view.Id && clock.GetUtcNow() < view.ExpiresAt && offset >= 0 && offset <= view.Items.Length, "snapshotRequired", 409);
            List<CatalogEntry> items = [];
            int bytes = 0;
            foreach (CatalogEntry item in view.Items.Skip(offset).Take(100))
            {
                int size = System.Text.Json.JsonSerializer.SerializeToUtf8Bytes(item, Wire.Json).Length + 1;
                if (bytes + size > Wire.MaximumBytes - 1024) { break; }
                items.Add(item); bytes += size;
            }
            int next = offset + items.Count;
            return new(1, room.Epoch, view.Snapshot.Sequence, items.ToArray(), next < view.Items.Length ? view.Id + ":" + next : null);
        }
    }

    public object RotateInvitation(string roomId, string token)
    {
        lock (gate)
        {
            (Room room, Member member) = Authorize(roomId, token);
            Wire.Need(member.Owner, "forbidden", 403);
            string invitation = Wire.Secret();
            room.Invitation = Wire.Hash(invitation);
            room.InvitationExpiry = clock.GetUtcNow() + limits.InvitationLifetime;
            return new { invitation, expiresAt = room.InvitationExpiry };
        }
    }

    public void Leave(string roomId, string token, string? revokeMemberId = null)
    {
        lock (gate)
        {
            (Room room, Member caller) = Authorize(roomId, token);
            Wire.Need(revokeMemberId is null || caller.Owner, "forbidden", 403);
            Member target = caller;
            if (revokeMemberId is not null)
            {
                Wire.Need(room.Members.TryGetValue(revokeMemberId, out target!), "unavailable", 404);
                Wire.Need(!target.Owner, "forbidden", 403);
            }
            Remove(room, target);
        }
    }

    public RoomStream Attach(string roomId, string token, string? epoch, long? cursor)
    {
        lock (gate)
        {
            (Room room, Member member) = Authorize(roomId, token);
            Wire.Need(member.Stream is null, "conflict", 409);
            if (cursor is not null)
            {
                Wire.Need(epoch == room.Epoch, "staleEpoch", 409);
                long oldest = room.Events.TryPeek(out var first) ? first.Event.Sequence : room.Sequence + 1;
                Wire.Need(cursor >= oldest - 1 && cursor <= room.Sequence, "snapshotRequired", 409);
            }
            // Replay may exceed a live queue: require a fresh snapshot rather than truncate it.
            RoomEvent[] replay = cursor is null ? [] : room.Events.Where(item => item.Event.Sequence > cursor).Select(item => item.Event).ToArray();
            Wire.Need(replay.Length <= limits.QueueFrames, "snapshotRequired", 409);
            RoomStream stream = new(limits.QueueFrames);
            if (cursor is null) { stream.Enqueue(new { v = 1, type = "room.bootstrap", data = BootstrapCore(room, member) }); }
            else { foreach (RoomEvent item in replay) { stream.Enqueue(item); } }
            member.Stream = stream;
            member.DisconnectedDeadline = null;
            return stream;
        }
    }

    public void Detach(string roomId, RoomStream stream)
    {
        lock (gate)
        {
            if (rooms.TryGetValue(roomId, out Room? room))
            {
                Member? member = room.Members.Values.FirstOrDefault(item => ReferenceEquals(item.Stream, stream));
                if (member is not null) { member.Stream = null; member.DisconnectedDeadline = clock.GetUtcNow() + limits.DisconnectGrace; }
            }
            stream.Stop();
        }
    }

    public CommandResult Execute(string roomId, string token, Command command)
    {
        lock (gate)
        {
            (Room room, Member member) = Authorize(roomId, token);
            DateTimeOffset now = clock.GetUtcNow();
            if (now - member.RateWindow >= TimeSpan.FromSeconds(1)) { member.RateWindow = now; member.RateCount = 0; }
            Wire.Need(++member.RateCount <= limits.CommandsPerSecond, "queueFull", 429);
            if (member.Results.TryGetValue(command.Number, out var cached))
            {
                Wire.Need(cached.Digest == command.Digest, "conflict", 409);
                return cached.Result;
            }
            Wire.Need(command.Number > member.HighestCommand, "duplicate", 409);
            string? error = null;
            try
            {
                Wire.Need(command.ClientRunId == member.Binding.ClientRunId, "foreignRun", 409);
                Wire.Need(command.RoomEpoch == room.Epoch, "staleEpoch", 409);
                Wire.Need(command.WatchGeneration == room.Generation, "staleGeneration", 409);
                Wire.Need(room.Sequence < Wire.MaximumCounter, "unavailable", 503);
                Apply(room, member, command);
            }
            catch (RoomException rejection) { error = rejection.Code; }
            CommandResult result = new(1, "command.result", command.Id, room.Sequence, error);
            member.HighestCommand = command.Number;
            member.Results.Add(command.Number, (command.Digest, result)); member.ResultOrder.Enqueue(command.Number);
            while (member.ResultOrder.Count > 128) { member.Results.Remove(member.ResultOrder.Dequeue()); }
            return result;
        }
    }

    private void Apply(Room room, Member member, Command command)
    {
        var payload = command.Payload;
        switch (command.Type)
        {
            case "catalog.updated":
                Wire.Fields(payload, "batchId", "offset", "items", "final");
                string batch = Wire.String(payload, "batchId");
                long offset = Wire.Integer(payload, "offset", limits.CatalogItems);
                Wire.Need(Wire.Id(batch) && payload.GetProperty("items").ValueKind == System.Text.Json.JsonValueKind.Array);
                MediaDescriptor[] items = payload.GetProperty("items").EnumerateArray().Select(Wire.Media).ToArray();
                Wire.Need(items.Length <= 100);
                bool final = Wire.Boolean(payload, "final");
                MediaDescriptor[] previous = offset == 0 ? [] : member.Pending;
                Wire.Need(offset == 0 || (member.PendingBatch == batch && offset == previous.Length), "conflict", 409);
                MediaDescriptor[] combined = [.. previous, .. items];
                Wire.Need(combined.Length <= limits.CatalogItems, "queueFull", 429);
                Wire.Need(combined.Select(item => item.MediaId).Distinct(StringComparer.Ordinal).Count() == combined.Length, "conflict", 409);
                if (!final) { member.PendingBatch = batch; member.Pending = combined; break; }
                List<MediaDescriptor[]> chunks = Chunk(combined);
                Wire.Need(room.Sequence <= Wire.MaximumCounter - chunks.Count, "unavailable", 503);
                member.Published = combined; member.PendingBatch = null; member.Pending = [];
                for (int index = 0; index < chunks.Count; index++)
                {
                    Publish(room, member, "catalog.updated", new { batchId = batch, items = chunks[index], final = index == chunks.Count - 1 });
                }
                break;
            case "selection.changed":
            case "watch.started":
                Wire.Fields(payload, "mediaId");
                string mediaId = Wire.String(payload, "mediaId");
                Wire.Need(Wire.Id(mediaId));
                CatalogEntry? selected = room.Selection;
                if (command.Type == "selection.changed")
                {
                    CatalogEntry[] matches = room.Members.Values.SelectMany(item => item.Published
                        .Where(media => media.MediaId == mediaId).Select(media => new CatalogEntry(item.Binding.MemberId, media))).Take(2).ToArray();
                    Wire.Need(matches.Length > 0, "unavailable", 409);
                    Wire.Need(matches.Length == 1, "conflict", 409);
                    selected = matches[0];
                }
                else { Wire.Need(selected?.Media.MediaId == mediaId && room.Watch.Status is "selected" or "ended", "invalidState", 409); }
                Wire.Need(room.Generation < Wire.MaximumCounter, "unavailable", 503);
                room.Generation++;
                room.Selection = selected;
                room.Watch = new(command.Type == "watch.started" ? "active" : "selected", mediaId);
                foreach (Member item in room.Members.Values) { item.Missing = false; }
                Publish(room, member, command.Type, new { mediaId });
                break;
            case "watch.ended":
                Wire.Fields(payload, "reason");
                string reason = Wire.String(payload, "reason");
                Wire.Need(reason is "user" or "finished");
                Wire.Need(member.Owner, "forbidden", 403);
                Wire.Need(room.Watch.Status == "active" && room.Generation < Wire.MaximumCounter, "invalidState", 409);
                room.Generation++; room.Watch = new("ended", room.Watch.MediaId);
                Publish(room, member, command.Type, new { reason });
                break;
            case "media.missing":
                Wire.Fields(payload, "mediaId", "missing");
                string missingId = Wire.String(payload, "mediaId");
                bool missing = Wire.Boolean(payload, "missing");
                Wire.Need(Wire.Id(missingId) && room.Watch.MediaId == missingId, "invalidState", 409);
                member.Missing = missing;
                Publish(room, member, command.Type, new { mediaId = missingId, missing });
                break;
            case "call.linkChanged":
                Wire.Fields(payload, "meetUri");
                string? uri = payload.GetProperty("meetUri").ValueKind == System.Text.Json.JsonValueKind.Null ? null : Wire.String(payload, "meetUri");
                Wire.Need(uri is null || System.Text.RegularExpressions.Regex.IsMatch(uri, @"\Ahttps://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}\z"));
                Wire.Need(member.Owner, "forbidden", 403);
                room.MeetUri = uri; Publish(room, member, command.Type, new { meetUri = uri });
                break;
            default: throw new RoomException("malformed", 400);
        }
    }

    private static List<MediaDescriptor[]> Chunk(MediaDescriptor[] source)
    {
        List<MediaDescriptor[]> chunks = [];
        List<MediaDescriptor> current = [];
        int bytes = 0;
        foreach (MediaDescriptor item in source)
        {
            int size = System.Text.Json.JsonSerializer.SerializeToUtf8Bytes(item, Wire.Json).Length + 1;
            if (current.Count == 100 || bytes + size > Wire.MaximumBytes - 1024) { chunks.Add(current.ToArray()); current.Clear(); bytes = 0; }
            current.Add(item); bytes += size;
        }
        if (current.Count > 0 || chunks.Count == 0) { chunks.Add(current.ToArray()); }
        return chunks;
    }

    private void Publish(Room room, Member member, string type, object payload)
    {
        RoomEvent value = new(1, type, room.Epoch, ++room.Sequence, member.Binding.MemberId,
            member.Binding.ClientRunId, room.Generation, payload);
        int bytes = System.Text.Json.JsonSerializer.SerializeToUtf8Bytes(value, Wire.Json).Length;
        Wire.Need(bytes <= Wire.MaximumBytes, "frameTooLarge", 413);
        room.Events.Enqueue((value, bytes)); room.EventBytes += bytes;
        while (room.Events.Count > limits.ReplayEvents || room.EventBytes > limits.ReplayBytes) { room.EventBytes -= room.Events.Dequeue().Bytes; }
        foreach (Member item in room.Members.Values) { item.Stream?.Enqueue(value); }
    }

    private Room Find(string roomId)
    {
        Wire.Need(Wire.Id(roomId) && rooms.ContainsKey(roomId), "unavailable", 404);
        return rooms[roomId];
    }

    private (Room, Member) Authorize(string roomId, string token)
    {
        SweepCore();
        Room room = Find(roomId);
        Member? member = room.Members.Values.FirstOrDefault(item => Wire.SecretMatches(token, item.Token));
        Wire.Need(member is not null, "unauthorized", 401);
        return (room, member!);
    }

    private void Remove(Room room, Member member)
    {
        RoomStream? revoked = member.Stream;
        member.Stream = null;
        revoked?.Stop();
        Publish(room, member, "member.left", new Dictionary<string, object>());
        room.Members.Remove(member.Binding.MemberId);
        if (member.Owner)
        {
            foreach (Member item in room.Members.Values) { item.Stream?.Stop(); }
            rooms.Remove(room.Id);
        }
    }

    public void Sweep() { lock (gate) { SweepCore(); } }
    private void SweepCore()
    {
        DateTimeOffset now = clock.GetUtcNow();
        foreach (Room room in rooms.Values.ToArray())
        {
            if (now >= room.ExpiresAt)
            {
                foreach (Member member in room.Members.Values) { member.Stream?.Stop(); }
                rooms.Remove(room.Id); continue;
            }
            foreach (Member member in room.Members.Values.ToArray())
            {
                if (member.DisconnectedDeadline is not null && now >= member.DisconnectedDeadline) { Remove(room, member); }
                if (!rooms.ContainsKey(room.Id)) { break; }
                if (member.View is not null && now >= member.View.ExpiresAt) { member.View = null; }
            }
        }
    }
}
