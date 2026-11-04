using System.Windows.Controls;

namespace SyncWatch.Design;

public sealed class AccountPresentation
{
    public override string ToString() => "[account presentation redacted]";
    public string Service { get; set; } = "";
    public string BrowserProof { get; set; } = "";
    public string State { get; set; } = "signedOut";
    public string Name { get; set; } = "";
    public string Email { get; set; } = "";
    public string Status { get; set; } = "accountSignedOut";
    public DesignContext Context
    {
        get
        {
            bool codeRequired = State == "browserConfirmationRequired";
            bool pending = State is "pending" or "authorizing", confirmation = State == "confirmAccount", signedIn = State == "signedIn", canStart = State is "signedOut" or "failed";
            static string Flag(bool value) => value ? "true" : "false";
            return new()
            {
                Values = new()
                {
                    ["namespace"] = "account",
                    ["service"] = Service,
                    ["browserProof"] = BrowserProof,
                    ["name"] = Name,
                    ["email"] = Email,
                    ["status"] = Status,
                    ["pending"] = Flag(pending),
                    ["confirmation"] = Flag(confirmation),
                    ["codeRequired"] = Flag(codeRequired),
                    ["signedIn"] = Flag(signedIn),
                    ["hasProfile"] = Flag(confirmation || signedIn),
                    ["showSignIn"] = Flag(canStart),
                    ["canCancel"] = Flag(pending || codeRequired || confirmation),
                    ["can.editAccountService"] = Flag(canStart),
                    ["can.accountSignIn"] = Flag(canStart && Service.Length > 0),
                    ["can.accountOpenBrowser"] = Flag(State == "pending"),
                    ["can.accountConfirm"] = Flag(confirmation),
                    ["can.accountVerifyBrowserProof"] = Flag(codeRequired && BrowserProof.Length > 0),
                    ["can.editAccountBrowserProof"] = Flag(codeRequired),
                    ["can.accountCancel"] = Flag(pending || codeRequired || confirmation),
                    ["can.accountSignOut"] = Flag(signedIn),
                    ["can.close"] = "true"
                }
            };
        }
    }
}
public sealed class AccountDialog : UserControl
{
    private readonly DesignSurface surface = new("Account");
    public event Action<string, string?>? ActionRequested;
    public AccountDialog() { Content = surface; surface.ConfigureHost(this); surface.EventRequested += e => ActionRequested?.Invoke(e.Action, e.Value); }
    public void Render(AccountPresentation presentation, string language) => surface.Render(presentation.Context, language);
}
