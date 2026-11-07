using System.Runtime.InteropServices;

namespace SyncWatch.Room;

public sealed class AccountVaultException : Exception
{
    public int? NativeErrorCode { get; }
    public AccountVaultException(int? nativeErrorCode = null) : base("Account credential storage unavailable" +
        (nativeErrorCode is int code ? " (OS code " + code + ")" : ""))
    { NativeErrorCode = nativeErrorCode; }
}

/// <summary>Local-machine Windows Credential Manager storage. No token enters a preference file or diagnostic.</summary>
public sealed class AccountCredentialVault
{
    private readonly string target;
    public AccountCredentialVault(string scope)
    {
        if (scope.Length != 64 || scope.Any(c => c is not (>= '0' and <= '9') and not (>= 'a' and <= 'f')))
            throw new ArgumentException("Invalid account storage scope", nameof(scope));
        target = "SyncWatch/device-session/v1/" + scope;
    }
    public byte[]? Read()
    {
        if (!OperatingSystem.IsWindows()) throw new AccountVaultException();
        if (!CredRead(target, 1, 0, out nint pointer))
        {
            int error = Marshal.GetLastPInvokeError();
            if (error == 1168) return null;
            throw new AccountVaultException(error);
        }
        try
        {
            Credential credential = Marshal.PtrToStructure<Credential>(pointer);
            if (credential.Type != 1 || credential.BlobSize is < 1 or > 2048 || credential.Blob == 0)
                throw new AccountVaultException();
            byte[] data = new byte[credential.BlobSize];
            Marshal.Copy(credential.Blob, data, 0, data.Length);
            return data;
        }
        finally { CredFree(pointer); }
    }
    public void Write(byte[] data)
    {
        if (data.Length is < 1 or > 2048) throw new ArgumentException("Invalid account storage record", nameof(data));
        if (!OperatingSystem.IsWindows()) throw new AccountVaultException();
        nint pointer = Marshal.AllocHGlobal(data.Length);
        try
        {
            Marshal.Copy(data, 0, pointer, data.Length);
            Credential credential = new()
            {
                Type = 1,
                Target = target,
                BlobSize = (uint)data.Length,
                Blob = pointer,
                Persist = 2,
                UserName = "SyncWatch"
            };
            if (!CredWrite(ref credential, 0)) throw new AccountVaultException(Marshal.GetLastPInvokeError());
        }
        finally { Marshal.Copy(new byte[data.Length], 0, pointer, data.Length); Marshal.FreeHGlobal(pointer); }
    }
    public void Remove()
    {
        if (!OperatingSystem.IsWindows()) throw new AccountVaultException();
        if (!CredDelete(target, 1, 0))
        {
            int error = Marshal.GetLastPInvokeError();
            if (error != 1168) throw new AccountVaultException(error);
        }
    }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct Credential
    {
        public uint Flags, Type;
        [MarshalAs(UnmanagedType.LPWStr)] public string? Target;
        [MarshalAs(UnmanagedType.LPWStr)] public string? Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public uint BlobSize;
        public nint Blob;
        public uint Persist, AttributeCount;
        public nint Attributes, Alias;
        [MarshalAs(UnmanagedType.LPWStr)] public string? UserName;
    }
    [DllImport("advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CredRead(string target, uint type, uint flags, out nint credential);
    [DllImport("advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CredWrite(ref Credential credential, uint flags);
    [DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CredDelete(string target, uint type, uint flags);
    [DllImport("advapi32.dll")] private static extern void CredFree(nint buffer);
}
