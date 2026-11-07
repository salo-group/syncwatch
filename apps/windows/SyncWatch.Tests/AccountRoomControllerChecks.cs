using System.IO;
using System.Text;
using System.Text.Json;
using SyncWatch.Room;

namespace SyncWatch;

internal static class AccountRoomControllerChecks
{
    internal static async Task RunAsync()
    {
        string? path = Environment.GetEnvironmentVariable("SYNCWATCH_ROOM_ACCOUNT_APP_FIXTURE");
        if (path is null) { Console.WriteLine("SKIP account room application: no owned service fixture"); return; }
        using var fixture = JsonDocument.Parse(await File.ReadAllBytesAsync(path));
        var ownerRecord = AccountSavedSession.Decode(Encoding.UTF8.GetBytes(fixture.RootElement.GetProperty("owner").GetRawText()));
        var guestRecord = AccountSavedSession.Decode(Encoding.UTF8.GetBytes(fixture.RootElement.GetProperty("guest").GetRawText()));
        using var ownerClient = new AccountClient(new(ownerRecord.Service));
        using var guestClient = new AccountClient(new(guestRecord.Service));
        var ownerDevice = await ownerClient.ResumeAsync(ownerRecord);
        var guestDevice = await guestClient.ResumeAsync(guestRecord);
        using var ownerAuthority = ownerClient.RoomAuthority(ownerDevice);
        using var guestAuthority = guestClient.RoomAuthority(guestDevice);
        RoomAccountAuthority? ownerSource = ownerAuthority, guestSource = guestAuthority;
        var host = new RoomController(accountSource: () => ownerSource);
        var peer = new RoomController(accountSource: () => guestSource);
        try
        {
            Require(!host.Editor.IsValid && host.Editor.RequiresAccount);
            host.Connect(); Require(host.Editor.State == "disconnected");
            host.BindAccount(ownerAuthority, ownerDevice.Account.Name);
            peer.BindAccount(guestAuthority, guestDevice.Account.Name);
            host.Connect(); await Settles(() => host.Connected);
            Require(host.Editor.Owner && host.Editor.AccountName == ownerDevice.Account.Name);
            var superseded = await host.InvitationForCopyAsync() ?? throw new Exception("No invitation");
            Require(await host.InvitationForCopyAsync() is not null && !host.CanCopy(superseded));
            foreach (string outcome in new[] { "cancel", "deny", "approve" })
            {
                var transfer = await host.InvitationForCopyAsync() ?? throw new Exception("No invitation");
                Require(host.CanCopy(transfer));
                peer.Editor.SelectMode("joinRoom"); peer.PasteInvitation(transfer.Invitation.CopyText()); peer.Connect();
                await Settles(() => peer.Editor.State == "awaitingApproval" && host.Editor.PendingRequests.Count == 1 && host.Editor.PendingAvailable);
                Require(host.Editor.PendingRequests[0].Name == guestDevice.Account.Name && host.Editor.PendingRequests[0].Email == guestDevice.Account.Email);
                if (outcome == "cancel")
                {
                    await peer.CloseAsync(); await peer.CloseAsync();
                    await Settles(() => host.Editor.PendingRequests.Count == 0); Require(host.Connected);
                }
                else
                {
                    host.DecideAdmission(host.Editor.PendingRequests[0].Id, outcome == "approve");
                    if (outcome == "deny")
                    {
                        await Settles(() => peer.Editor.State == "failed");
                        Require(peer.Editor.Status == "roomAdmissionDenied"); await peer.CloseAsync();
                    }
                    else
                    {
                        await Settles(() => peer.Connected && host.Editor.MemberCount == 2); Require(!peer.Editor.Owner);
                        var membership = (await peer.BeginWatchAsync()).Membership;
                        Require(peer.CanContinuePlayback(membership));
                        if (fixture.RootElement.TryGetProperty("browserFixtureToken", out var browserToken))
                            await MeetControllerChecks.Run(host, peer, new Uri(ownerRecord.Service), browserToken.GetString()!, ownerDevice.Account,
                                available => { guestSource = available ? guestAuthority : null; ownerSource = available ? ownerAuthority : null; });
                        guestSource = null;
                        Require(!peer.Connected && !peer.CanContinuePlayback(membership)); ownerSource = null;
                        Require(!host.Connected && !host.CanCopy(transfer));
                        await peer.CloseAsync(); await host.CloseAsync(); host.InvitationCopyCompleted(true, transfer);
                        Require(host.Editor.State == "disconnected" && host.Editor.Status.Length == 0);
                    }
                }
            }
            await guestClient.LogoutAsync(guestDevice); await ownerClient.LogoutAsync(ownerDevice);
            Console.WriteLine("SYNCWATCH_ACCOUNT_ROOM_APPLICATION_OK");
        }
        finally { await peer.CloseAsync(); await host.CloseAsync(); }
    }
    private static void Require(bool value) { if (!value) throw new Exception("Account room application invariant"); }
    private static async Task Settles(Func<bool> condition)
    {
        long deadline = Environment.TickCount64 + 20000;
        while (!condition()) { if (Environment.TickCount64 >= deadline) throw new TimeoutException("Account room application"); await Task.Delay(20); }
    }
}
