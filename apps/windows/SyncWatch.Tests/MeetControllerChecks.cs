using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using SyncWatch.Room;

namespace SyncWatch;

internal static class MeetControllerChecks
{
    internal static async Task Run(RoomController host, RoomController peer, Uri service, string fixtureToken, AccountProfile account, Action<bool> setAccountAvailable)
    {
        Uri? authorization = null; int opens = 0;
        var entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        var owner = new MeetController(host, async (uri, _) =>
        {
            if (uri.GetLeftPart(UriPartial.Authority) == service.GetLeftPart(UriPartial.Authority) && uri.AbsolutePath == "/auth/google/meet-start") { authorization = uri; return true; }
            Require(uri.Host == "meet.google.com"); opens++; entered.TrySetResult(true); await release.Task; return true;
        });
        var guest = new MeetController(peer, (_, _) => throw new Exception("Guest browser must remain explicit"));
        try
        {
            owner.Present(); guest.Present();
            await Settles(() => owner.Presentation.EnabledActions.Contains("meetAuthorize") && guest.Presentation.Status == "meetIdle");
            Require(!guest.Presentation.EnabledActions.Overlaps(["meetAuthorize", "meetStart", "meetEnd"]));
            owner.Action("meetAuthorize"); await Settles(() => owner.Presentation.EnabledActions.Contains("meetOpenAuthorization"));
            Require(owner.Presentation.Name.Length == 0 && owner.Presentation.Email.Length == 0);
            owner.Action("meetOpenAuthorization"); await Settles(() => authorization is not null);
            string code = await CompleteBrowser(service, authorization!, fixtureToken);
            await Settles(() => owner.Presentation.AuthorizationState == "browserConfirmationRequired");
            Require(owner.Presentation.Name.Length == 0);
            owner.Action("editMeetBrowserProof", code); owner.Action("meetVerify");
            await Settles(() => owner.Presentation.AuthorizationState == "confirmAuthorization");
            Require(owner.Presentation.Name == account.Name && owner.Presentation.Email == account.Email);
            setAccountAvailable(false);
            Require(owner.Presentation.Name.Length == 0 && owner.Presentation.Email.Length == 0 && !owner.Presentation.EnabledActions.Contains("meetConfirm"));
            setAccountAvailable(true);
            owner.Action("meetConfirm"); await Settles(() => owner.Presentation.EnabledActions.Contains("meetStart"));
            Require(owner.Presentation.Name.Length == 0);
            owner.Action("meetStart"); await Settles(() => owner.Presentation.EnabledActions.Contains("meetOpen") && guest.Presentation.EnabledActions.Contains("meetOpen"));
            Require(!guest.Presentation.EnabledActions.Contains("meetEnd"));
            owner.Action("meetEnd"); Require(owner.Presentation.EnabledActions.Contains("meetConfirmEnd"));
            owner.Action("meetCancelEnd"); Require(owner.Presentation.EnabledActions.Contains("meetEnd"));
            Require((await host.CallMembership()!.Owner.CallStatusAsync()).State == "ready");
            owner.Action("meetEnd"); owner.Action("meetConfirmEnd");
            await Settles(() => owner.Presentation.Status == "meetEnded" && guest.Presentation.Status == "meetEnded");
            Require(host.Connected && peer.Connected);
            owner.Action("meetStart"); await Settles(() => owner.Presentation.EnabledActions.Contains("meetOpen"));
            owner.Action("meetRetry"); await Settles(() => owner.Presentation.EnabledActions.Contains("meetOpen"));
            var beforeRemoteEnd = await host.CallMembership()!.Owner.CallStatusAsync();
            var remoteEnd = host.CallMembership()!.Owner.CallEndOperation(beforeRemoteEnd);
            var remotelyEnded = await host.CallMembership()!.Owner.RequestCallAsync(remoteEnd);
            using var endTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
            while (remotelyEnded.State == "ending") { await Task.Delay(100, endTimeout.Token); remotelyEnded = await host.CallMembership()!.Owner.CallStatusAsync(endTimeout.Token); }
            Require(remotelyEnded.State == "ended" && owner.Presentation.EnabledActions.Contains("meetOpen"));
            owner.Action("meetOpen"); await Settles(() => owner.Presentation.Status == "meetEnded"); Require(opens == 0);
            owner.Action("meetStart"); await Settles(() => owner.Presentation.EnabledActions.Contains("meetOpen"));
            owner.Action("meetOpen"); owner.Action("meetOpen");
            await entered.Task.WaitAsync(TimeSpan.FromSeconds(5)); Require(opens == 1);
            setAccountAvailable(false);
            Require(owner.Presentation.Name.Length == 0 && !owner.Presentation.EnabledActions.Contains("meetOpen") && !guest.Presentation.EnabledActions.Contains("meetConfirm"));
            owner.MembershipChanged(); guest.MembershipChanged();
            Task close = owner.CloseAsync(); await Task.Yield(); Require(!close.IsCompleted);
            release.TrySetResult(true); await close;
            Require(owner.Presentation.Status != "meetHandedOff" && opens == 1);
            Console.WriteLine("SYNCWATCH_MEET_APPLICATION_OK synthetic provider, profile fence, owner/guest, end confirmation, explicit single-flight handoff and late scope loss");
        }
        finally { release.TrySetResult(true); await owner.CloseAsync(); await guest.CloseAsync(); }
    }
    private static async Task<string> CompleteBrowser(Uri service, Uri authorization, string fixtureToken)
    {
        Require(authorization.Query.StartsWith("?id=", StringComparison.Ordinal));
        using var client = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false, UseCookies = false }) { Timeout = TimeSpan.FromSeconds(5) };
        using var response = await client.PostAsJsonAsync(new Uri(service, "/__test/meet-browser"), new { v = 2, transactionId = authorization.Query[4..], scenario = "accept", fixtureToken });
        response.EnsureSuccessStatusCode(); using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        return document.RootElement.GetProperty("result").GetProperty("browserProof").GetString() ?? throw new Exception("Synthetic browser proof missing");
    }
    private static async Task Settles(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(25));
        while (!condition()) await Task.Delay(20, timeout.Token);
    }
    private static void Require(bool value) { if (!value) throw new Exception("Meet application invariant"); }
}
