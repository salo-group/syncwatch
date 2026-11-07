using System.Collections.ObjectModel;
using System.Windows.Controls;

namespace SyncWatch.Design;

public sealed record RoomAdmissionRow(string Id, string Name, string Email);

/// <summary>Memory-only connection draft; never persists credentials.</summary>
public sealed class RoomEditor
{
    private readonly Dictionary<string, string> values = new(SyncWatchRoomForm.Defaults);
    public IReadOnlyDictionary<string, string> Values => new ReadOnlyDictionary<string, string>(new Dictionary<string, string>(values));
    public string Mode { get; private set; } = "createRoom";
    public string State { get; set; } = "disconnected";
    public string Status { get; set; } = "";
    public string RoomId { get; set; } = "";
    public int MemberCount { get; set; }
    public bool Owner { get; set; }
    public string Flow { get; }
    public bool AccountReady { get; set; }
    public string AccountName { get; set; } = "";
    public string AccountService { get; set; } = "";
    public IReadOnlyList<RoomAdmissionRow> PendingRequests { get; set; } = [];
    public bool PendingAvailable { get; set; } = true;
    public bool Deciding { get; set; }
    public string PendingStatus { get; set; } = "";
    public bool RequiresAccount => SyncWatchRoomForm.SignInFlows.Contains(Flow);
    private bool NeedsAccount => RequiresAccount && !AccountReady;
    public bool Busy => State is "connecting" or "reconnecting" or "awaitingApproval";
    public bool Connected => State == "connected";
    public SyncWatchRoomFormField[] Fields => SyncWatchRoomForm.Fields.Where(p => (SyncWatchRoomForm.Flows.GetValueOrDefault(Flow) ?? []).Contains(p.Id) && (p.Section == "connection" || p.Section == Mode)).ToArray();
    public RoomEditor(IReadOnlyDictionary<string, string>? initial = null, string flow = SyncWatchRoomForm.DefaultFlow)
    {
        Flow = flow;
        foreach (var descriptor in SyncWatchRoomForm.Fields.Where(p => p.Kind != "secret"))
            if (initial?.TryGetValue(descriptor.Id, out var value) == true) values[descriptor.Id] = value;
    }
    public bool IsValid => SyncWatchRoomForm.Flows.ContainsKey(Flow) && !NeedsAccount && Fields.All(descriptor => values.TryGetValue(descriptor.Id, out var value) && value.Length is > 0
        && value.Length <= descriptor.MaxLength && !value.Any(c => c < 32 || c == 127));
    public void Edit(string id, string value)
    {
        if (Busy || Connected || NeedsAccount || Fields.FirstOrDefault(p => p.Id == id) is not { } descriptor || value.Length > descriptor.MaxLength) return;
        values[id] = value; Status = "";
    }
    public void SelectMode(string mode)
    {
        if (Busy || Connected || NeedsAccount || mode == Mode || mode is not ("createRoom" or "joinRoom")) return;
        ClearSecrets(); Mode = mode; Status = "";
    }
    public void ClearSecrets() { foreach (var descriptor in SyncWatchRoomForm.Fields.Where(p => p.Kind == "secret")) values[descriptor.Id] = ""; }
    public DesignContext Context => new()
    {
        Values = new()
        {
            ["namespace"] = "room",
            ["showForm"] = (!Connected && State is not ("reconnecting" or "awaitingApproval") && !NeedsAccount).ToString().ToLowerInvariant(),
            ["needsAccount"] = NeedsAccount.ToString().ToLowerInvariant(),
            ["showAccountIdentity"] = (RequiresAccount && AccountReady).ToString().ToLowerInvariant(),
            ["accountName"] = AccountName,
            ["accountService"] = AccountService,
            ["showPendingRequests"] = (Connected && Owner && RequiresAccount).ToString().ToLowerInvariant(),
            ["pendingStatus"] = PendingStatus,
            ["connected"] = (Connected || State == "reconnecting").ToString().ToLowerInvariant(),
            ["createMode"] = (Mode == "createRoom").ToString().ToLowerInvariant(),
            ["joinMode"] = (Mode == "joinRoom").ToString().ToLowerInvariant(),
            ["connectLabel"] = Mode,
            ["status"] = SyncWatchRoomForm.StatusCopy.GetValueOrDefault(State) ?? Status,
            ["roomID"] = RoomId,
            ["memberCount"] = MemberCount.ToString(System.Globalization.CultureInfo.InvariantCulture),
            ["can.close"] = "true",
            ["can.openAccount"] = (NeedsAccount && !Busy).ToString().ToLowerInvariant(),
            ["can.cancelRequest"] = (State == "awaitingApproval").ToString().ToLowerInvariant(),
            ["can.chooseRoomMode"] = (!Busy && !Connected && !NeedsAccount).ToString().ToLowerInvariant(),
            ["can.connectRoom"] = (!Busy && !Connected && IsValid).ToString().ToLowerInvariant(),
            ["can.leaveRoom"] = (Connected || State is "failed" or "reconnecting").ToString().ToLowerInvariant(),
            ["can.copyInvitation"] = (Connected && Owner).ToString().ToLowerInvariant(),
            ["can.pasteInvitation"] = (!Busy && !Connected && Mode == "joinRoom" && !NeedsAccount).ToString().ToLowerInvariant()
        },
        Collections = new()
        {
            ["fields"] = Fields.Select(p => new DesignContext
            {
                Values = new()
                {
                    ["id"] = p.Id,
                    ["label"] = p.Label,
                    ["value"] = values[p.Id],
                    ["secret"] = (p.Kind == "secret").ToString().ToLowerInvariant(),
                    ["editable"] = (!Busy).ToString().ToLowerInvariant()
                }
            }).ToArray(),
            ["pendingRequests"] = PendingRequests.Select(p => new DesignContext
            {
                Values = new()
                {
                    ["requestID"] = p.Id,
                    ["name"] = p.Name,
                    ["email"] = p.Email,
                    ["can.decide"] = (Connected && Owner && PendingAvailable && !Deciding).ToString().ToLowerInvariant()
                }
            }).ToArray()
        }
    };
}
public sealed class RoomDialog : UserControl
{
    public RoomEditor Editor { get; }
    private readonly DesignSurface surface = new("Room");
    public string UiLanguage { get; private set; }
    public event Action<string>? ActionRequested;
    public event Action<string, bool>? AdmissionDecisionRequested;
    public RoomDialog(RoomEditor editor, string language = "en")
    {
        Editor = editor; UiLanguage = language; Content = surface; surface.ConfigureHost(this);
        surface.EventRequested += e =>
        {
            if (e.Action == "editRoomField" && e.Payload is string id && e.Value is string value) Editor.Edit(id, value);
            else if (e.Action == "chooseRoomMode" && e.Payload is string mode) Editor.SelectMode(mode);
            else if (e.Action is "approveRoomAdmission" or "denyRoomAdmission" && e.Payload is string request)
                AdmissionDecisionRequested?.Invoke(request, e.Action == "approveRoomAdmission");
            else ActionRequested?.Invoke(e.Action);
            Refresh();
        };
        Refresh();
    }
    public void Refresh() => surface.Render(Editor.Context, UiLanguage);
}
