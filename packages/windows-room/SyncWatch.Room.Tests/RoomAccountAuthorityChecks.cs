using SyncWatch.Room;

internal static class RoomAccountAuthorityChecks
{
    internal static async Task Run()
    {
        var address = new RoomAddress("https://service.example.invalid/");
        Guid owner = Guid.NewGuid();
        var profile = new AccountProfile(new string('A', 43), "Owner", "owner@example.invalid");
        AccountDeviceSession Device(char value, Uri? origin = null, Guid? source = null) =>
            new(profile, new string(value, 43), new string('R', 43), origin ?? address.Origin, source ?? owner);
        using var authority = new RoomAccountAuthority(address, Device('B'), owner);
        AccountRoomCredential first = await authority.CredentialAsync();
        Require(!first.ToString().Contains(first.Access));
        authority.Pause();
        using var cancellation = new CancellationTokenSource();
        Task<AccountRoomCredential> cancelled = authority.CredentialAsync(cancellation.Token);
        Task<AccountRoomCredential> survivor = authority.CredentialAsync();
        cancellation.Cancel(); authority.Commit(Device('C'));
        try { _ = await cancelled; throw new Exception("Cancelled request acquired credentials"); } catch (OperationCanceledException) { }
        AccountRoomCredential rotated = await survivor;
        Require(rotated.Access == new string('C', 43));
        Reject(() => authority.Commit(Device('D', new Uri("https://other.example.invalid/"))), "malformed");
        Reject(() => authority.Commit(Device('D', source: Guid.NewGuid())), "malformed");
        Reject(() => authority.Commit(new(new(new string('Z', 43), "Other", "other@example.invalid"), new string('D', 43), new string('R', 43), address.Origin, owner)), "malformed");
        Require((await authority.CredentialAsync()).Access == rotated.Access);
        authority.Pause(); Task<AccountRoomCredential> failed = authority.CredentialAsync(); authority.Unavailable();
        try { _ = await failed; throw new Exception("Unavailable account authorized a room"); } catch (RoomProtocolException error) when (error.Message == "transport") { }
        authority.Pause(); Task<AccountRoomCredential> recovering = authority.CredentialAsync(); authority.Commit(Device('E'));
        Require((await recovering).Access == new string('E', 43));
        authority.Pause(); Task<AccountRoomCredential> closing = authority.CredentialAsync(); authority.Dispose();
        try { _ = await closing; throw new Exception("Closed authority authorized a room"); } catch (RoomProtocolException error) when (error.Message == "closed") { }
        Reject(() => authority.Commit(Device('F')), "closed");
        authority.Dispose();
        Console.WriteLine("SYNCWATCH_ROOM_ACCOUNT_AUTHORITY_OK");
    }
    private static void Require(bool value) { if (!value) throw new Exception("Room account authority invariant failed"); }
    private static void Reject(Action operation, string code)
    {
        try { operation(); } catch (RoomProtocolException error) when (error.Message == code) { return; }
        throw new Exception("Room account authority accepted " + code);
    }
}
