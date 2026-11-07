using SyncWatch.Design;

internal static class RoomAssertions
{
    internal static void Run()
    {
        void Check(bool value) { if (!value) throw new InvalidOperationException("Room draft capability/lifetime"); }
        var editor = new RoomEditor(new Dictionary<string, string> { ["username"] = "Alice", ["creationKey"] = "must not restore" }, flow: "legacy");
        Check(editor.Values["creationKey"] == "" && !editor.IsValid && editor.Context.Values["can.connectRoom"] == "false");
        editor.Edit("creationKey", "creation-secret"); editor.SelectMode("createRoom"); Check(editor.Values["creationKey"] == "creation-secret");
        editor.SelectMode("joinRoom"); Check(editor.Values["creationKey"] == "" && editor.Values["username"] == "Alice");
        Check(editor.Context.Values["can.pasteInvitation"] == "true");
        editor.Edit("invitation", "invitation-secret"); editor.State = "connecting";
        Check(editor.Context.Values["can.pasteInvitation"] == "false");
        editor.Edit("username", "Bob"); editor.SelectMode("createRoom"); Check(editor.Values["username"] == "Alice" && editor.Mode == "joinRoom");
        editor.ClearSecrets(); Check(editor.Values["invitation"] == "");
        editor.State = "connected"; editor.Owner = false; Check(editor.Context.Values["can.copyInvitation"] == "false");
        editor.Owner = true; Check(editor.Context.Values["can.copyInvitation"] == "true");
        editor.State = "reconnecting";
        Check(editor.Busy && !editor.Connected && editor.Context.Values["status"] == "reconnecting");
        Check(editor.Context.Values["showForm"] == "false" && editor.Context.Values["can.leaveRoom"] == "true");
        Check(editor.Context.Values["can.copyInvitation"] == "false" && editor.Context.Values["can.connectRoom"] == "false");
        editor.Edit("username", "Changed"); Check(editor.Values["username"] == "Alice");

        var account = new RoomEditor();
        Check(account.RequiresAccount && !account.IsValid && account.Context.Values["needsAccount"] == "true");
        Check(account.Context.Values["showForm"] == "false" && account.Context.Values["can.openAccount"] == "true");
        account.AccountReady = true; account.AccountName = "Owner"; account.AccountService = "https://service.example.invalid/";
        Check(account.IsValid && account.Fields.Length == 0);
        account.SelectMode("joinRoom"); Check(account.Fields.Select(p => p.Id).SequenceEqual(new[] { "roomID", "invitation" }));
        account.Edit("serviceAddress", "https://foreign.example.invalid/"); Check(account.Values["serviceAddress"] == "");
        account.Edit("roomID", "room-test"); account.Edit("invitation", new string('A', 64)); Check(account.IsValid);
        account.State = "awaitingApproval";
        Check(account.Busy && account.Context.Values["can.cancelRequest"] == "true" && account.Context.Values["status"] == "roomAdmissionPending" && account.Context.Values["showForm"] == "false");
        account.State = "connected"; account.Owner = true;
        account.PendingRequests = [new("approval-handle", "Guest", "guest@example.invalid")];
        Check(account.Context.Collections["pendingRequests"].Single().Values["requestID"] == "approval-handle");
        Check(account.Context.Collections["pendingRequests"].Single().Values["can.decide"] == "true");
        account.PendingAvailable = false; Check(account.Context.Collections["pendingRequests"].Single().Values["can.decide"] == "false");
        account.PendingAvailable = true; account.Deciding = true; Check(account.Context.Collections["pendingRequests"].Single().Values["can.decide"] == "false");
        Check(!new RoomEditor(flow: "unknown").IsValid);
    }
}
