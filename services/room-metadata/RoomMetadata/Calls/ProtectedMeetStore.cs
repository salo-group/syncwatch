using System.Text.Json;
using Microsoft.AspNetCore.DataProtection;
using SyncWatch.RoomMetadata.Accounts;

namespace SyncWatch.RoomMetadata.Calls;

// Separate cryptographic purposes and writer leases for grants and provider-operation outcomes.
internal sealed class ProtectedMeetStore<T> : IDisposable where T : class
{
    private readonly string path;
    private readonly IDataProtector protector;
    private readonly FileStream lease;
    private readonly Func<T, bool> validate;
    internal T Current { get; private set; }

    internal ProtectedMeetStore(string directory, string name, IDataProtectionProvider protection,
        Func<T> initial, Func<T, bool> validate)
    {
        string owned = Path.Combine(directory, "meet"); Directory.CreateDirectory(owned);
        path = Path.Combine(owned, name + ".protected");
        protector = protection.CreateProtector("SyncWatch.Meet", name, "v1");
        this.validate = validate;
        lease = new FileStream(Path.Combine(owned, name + ".writer.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        try
        {
            try
            {
                FileAttributes attributes = File.GetAttributes(path);
                AccountWire.Need((attributes & (FileAttributes.Directory | FileAttributes.ReparsePoint)) == 0,
                    "accountStoreInvalid", 503);
                AccountWire.Need(new FileInfo(path).Length <= MeetPolicy.MaximumStoreBytes, "accountStoreInvalid", 503);
                Current = JsonSerializer.Deserialize<T>(protector.Unprotect(File.ReadAllBytes(path)), AccountWire.Json)
                    ?? throw new AccountException("accountStoreInvalid", 503);
            }
            catch (FileNotFoundException) { Current = initial(); }
            catch (DirectoryNotFoundException) { Current = initial(); }
            AccountWire.Need(validate(Current), "accountStoreInvalid", 503);
        }
        catch { lease.Dispose(); throw; }
    }

    internal void Save(T replacement)
    {
        AccountWire.Need(validate(replacement), "accountStoreInvalid", 503);
        byte[] bytes = protector.Protect(JsonSerializer.SerializeToUtf8Bytes(replacement, AccountWire.Json));
        AccountWire.Need(bytes.Length <= MeetPolicy.MaximumStoreBytes, "accountCapacity", 503);
        string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (FileStream output = new(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
            { output.Write(bytes); output.Flush(flushToDisk: true); }
            File.Move(temporary, path, overwrite: true);
            Current = replacement;
        }
        finally { File.Delete(temporary); }
    }
    public void Dispose() => lease.Dispose();
}
