using System.Windows.Controls;

namespace SyncWatch.Design;

public sealed class MeetPresentation
{
    public override string ToString() => "[Meet presentation redacted]";
    public string AuthorizationState { get; set; } = "none";
    public string BrowserProof { get; set; } = "";
    public string Name { get; set; } = "";
    public string Email { get; set; } = "";
    public string Status { get; set; } = "meetRoomRequired";
    public HashSet<string> EnabledActions { get; set; } = ["close", "openRoom"];
    public DesignContext Context
    {
        get
        {
            Dictionary<string, string> values = new()
            {
                ["namespace"] = "meet",
                ["authorizationState"] = AuthorizationState,
                ["browserProof"] = BrowserProof,
                ["name"] = Name,
                ["email"] = Email,
                ["status"] = Status
            };
            foreach (string action in EnabledActions) values["can." + action] = "true";
            return new() { Values = values };
        }
    }
}
public sealed class MeetDialog : UserControl
{
    private readonly DesignSurface surface = new("Meet");
    public event Action<string, string?>? ActionRequested;
    public MeetDialog() { Content = surface; surface.ConfigureHost(this); surface.EventRequested += e => ActionRequested?.Invoke(e.Action, e.Value); }
    public void Render(MeetPresentation presentation, string language) => surface.Render(presentation.Context, language);
}
