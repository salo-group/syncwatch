using System.Text;
using System.Text.Json;
using SyncWatch.Room;

static class AccountSessionChecks
{
    public static void Run(string fixture)
    {
        using var json = JsonDocument.Parse(File.ReadAllBytes(fixture));
        AccountSavedSession? valid = null;
        foreach (var item in json.RootElement.GetProperty("cases").EnumerateArray())
        {
            bool accepted = false;
            AccountSavedSession? record = null;
            try { record = AccountSavedSession.Decode(Encoding.UTF8.GetBytes(item.GetProperty("wire").GetString()!)); accepted = true; valid ??= record; }
            catch (Exception) { }
            if (record?.ToString().Contains(new string('B', 43)) == true) throw new Exception("Saved credential is not redacted");
            if (accepted != item.GetProperty("valid").GetBoolean()) throw new Exception("Account checkpoint: " + item.GetProperty("name").GetString());
        }
        Console.WriteLine("SYNCWATCH_ACCOUNT_RECORDS_OK");
        if (Environment.GetEnvironmentVariable("SYNCWATCH_ACCOUNT_VAULT_CHECKS") != "1") return;
        string directory = Path.Combine(Path.GetTempPath(), "sw-account-session-" + Guid.NewGuid().ToString("N"));
        var first = new AccountSessionStore(directory); var second = new AccountSessionStore(directory);
        try
        {
            if (first.Read() is not null) throw new Exception("Profile was not empty");
            first.Save(valid!);
            bool denied = false;
            try { second.Read(); } catch (IOException) { denied = true; }
            if (!denied) throw new Exception("Second profile owner was admitted");
            first.Dispose(); second.Dispose();
            using var restored = new AccountSessionStore(directory);
            if (restored.Read()?.Service != valid!.Service) throw new Exception("Profile checkpoint was not restored");
            restored.Forget(); if (restored.Read() is not null) throw new Exception("Profile checkpoint survived deletion");
            Console.WriteLine("SYNCWATCH_ACCOUNT_PROFILE_RESTART_OK");
        }
        finally
        {
            first.Dispose(); second.Dispose();
            using (var cleanup = new AccountSessionStore(directory)) cleanup.Forget();
            Directory.Delete(directory, recursive: true);
        }
    }
}
