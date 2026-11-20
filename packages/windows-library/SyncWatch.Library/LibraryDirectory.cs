using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace SyncWatch.Library;

/// <summary>Enumerates one verified directory and opens child directories relative to its handle.</summary>
internal sealed class LibraryDirectory : IDisposable
{
    private const uint ListDirectory = 0x0001;
    private const uint ReadAttributes = 0x0080;
    private const uint Synchronize = 0x00100000;
    private const uint BackupSemantics = 0x02000000;
    private const uint OpenReparsePoint = 0x00200000;
    private const uint DirectoryFile = 0x00000001;
    private const uint SynchronousNonAlert = 0x00000020;
    private const uint CaseInsensitive = 0x00000040;
    private const int FileAttributeTagInfo = 9;
    private const int FileFullDirectoryInfo = 14;
    private const int FileFullDirectoryRestartInfo = 15;
    private const int FileNotFound = 2;
    private const int NoMoreFiles = 18;
    // Fixed fields in the FILE_FULL_DIR_INFO ABI precede the variable UTF-16 name.
    private const int LastWriteTimeOffset = 24;
    private const int EndOfFileOffset = 40;
    private const int AttributesOffset = 56;
    private const int FileNameLengthOffset = 60;
    private const int FileNameOffset = 68;
    private const int BufferSize = 64 * 1024;
    private const int ObjectNameNotFound = unchecked((int)0xC0000034);
    private const int ObjectPathNotFound = unchecked((int)0xC000003A);
    private const FileShare Share = FileShare.ReadWrite | FileShare.Delete;

    private readonly SafeFileHandle handle;

    private LibraryDirectory(SafeFileHandle handle)
    {
        this.handle = handle;
        try
        {
            if (handle.IsInvalid)
            {
                int error = Marshal.GetLastPInvokeError();
                if (error is FileNotFound or 3) throw new DirectoryNotFoundException("library-directory-open", new Win32Exception(error));
                if (error == 5) throw new UnauthorizedAccessException("library-directory-open", new Win32Exception(error));
                throw IoError("library-directory-open");
            }
            if (!GetFileInformationByHandleEx(handle, FileAttributeTagInfo, out AttributeTagInfo info, Marshal.SizeOf<AttributeTagInfo>()))
                throw IoError("library-directory-open");
            if ((info.Attributes & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != FileAttributes.Directory)
                throw new IOException("library-directory-boundary");
        }
        catch
        {
            handle.Dispose();
            throw;
        }
    }

    internal static LibraryDirectory OpenRoot(string path) =>
        new(CreateFile(Win32Path(Path.TrimEndingDirectorySeparator(path)), ListDirectory | ReadAttributes, Share, 0, FileMode.Open,
            BackupSemantics | OpenReparsePoint, 0));

    private static string Win32Path(string path)
    {
        if (path.Length < 260 || path.StartsWith(@"\\?\", StringComparison.Ordinal) || path.StartsWith(@"\\.\", StringComparison.Ordinal))
            return path;
        return path.StartsWith(@"\\", StringComparison.Ordinal) ? @"\\?\UNC\" + path[2..] : @"\\?\" + path;
    }

    internal LibraryDirectory OpenChild(string name)
    {
        SafeFileHandle? child = OpenRelative(name, ListDirectory | ReadAttributes, DirectoryFile);
        return new(child ?? throw new IOException("library-directory-changed"));
    }

    internal bool Exists(string name)
    {
        using SafeFileHandle? file = OpenRelative(name, ReadAttributes, 0);
        return file is not null;
    }

    private SafeFileHandle? OpenRelative(string name, uint access, uint options)
    {
        if (name is "." or ".." || name.AsSpan().ContainsAny('\\', '/', ':')) throw new IOException("library-entry-name");
        ushort bytes = checked((ushort)(name.Length * sizeof(char)));
        nint buffer = Marshal.StringToHGlobalUni(name);
        try
        {
            nint descriptor = Marshal.AllocHGlobal(Marshal.SizeOf<UnicodeString>());
            try
            {
                Marshal.StructureToPtr(new UnicodeString { Length = bytes, MaximumLength = bytes, Buffer = buffer }, descriptor, false);
                // The scanner owns the parent handle for the entire synchronous open.
                var attributes = new ObjectAttributes
                {
                    Length = Marshal.SizeOf<ObjectAttributes>(),
                    RootDirectory = handle.DangerousGetHandle(),
                    ObjectName = descriptor,
                    Attributes = CaseInsensitive
                };
                int status = NtCreateFile(out SafeFileHandle file, access | Synchronize, ref attributes, out _, 0, 0, Share,
                    1, options | OpenReparsePoint | SynchronousNonAlert, 0, 0);
                GC.KeepAlive(handle);
                if (status >= 0 && !file.IsInvalid) return file;
                file.Dispose();
                if (status is ObjectNameNotFound or ObjectPathNotFound) return null;
                throw new IOException("library-entry-open", new Win32Exception(unchecked((int)RtlNtStatusToDosError(status))));
            }
            finally { Marshal.FreeHGlobal(descriptor); }
        }
        finally { Marshal.FreeHGlobal(buffer); }
    }

    internal record struct Entry(string Name, FileAttributes Attributes, long Size, long LastWriteTime);

    internal List<Entry> ReadEntries(CancellationToken cancellation)
    {
        var entries = new List<Entry>();
        nint buffer = Marshal.AllocHGlobal(BufferSize);
        try
        {
            int infoClass = FileFullDirectoryRestartInfo;
            while (true)
            {
                cancellation.ThrowIfCancellationRequested();
                if (!GetFileInformationByHandleEx(handle, infoClass, buffer, BufferSize))
                {
                    int error = Marshal.GetLastPInvokeError();
                    if (error == NoMoreFiles || error == FileNotFound && infoClass == FileFullDirectoryRestartInfo) return entries;
                    throw IoError("library-directory-enumeration");
                }
                infoClass = FileFullDirectoryInfo;
                int offset = 0;
                while (true)
                {
                    int remaining = BufferSize - offset;
                    if (remaining < FileNameOffset) throw new IOException("library-directory-entry");
                    nint entry = buffer + offset;
                    int next = Marshal.ReadInt32(entry);
                    int nameBytes = Marshal.ReadInt32(entry, FileNameLengthOffset);
                    if (nameBytes <= 0 || nameBytes % sizeof(char) != 0 || nameBytes > remaining - FileNameOffset ||
                        next != 0 && (next < FileNameOffset + nameBytes || next > remaining - FileNameOffset))
                        throw new IOException("library-directory-entry");
                    string name = Marshal.PtrToStringUni(entry + FileNameOffset, nameBytes / sizeof(char));
                    if (name is not "." and not "..")
                    {
                        if (name.AsSpan().ContainsAny('\\', '/', ':') || name.Contains('\0')) throw new IOException("library-entry-name");
                        entries.Add(new(name, (FileAttributes)Marshal.ReadInt32(entry, AttributesOffset),
                            Marshal.ReadInt64(entry, EndOfFileOffset), Marshal.ReadInt64(entry, LastWriteTimeOffset)));
                    }
                    if (next == 0) break;
                    offset += next;
                }
            }
        }
        finally { Marshal.FreeHGlobal(buffer); }
    }

    private static IOException IoError(string code) => new(code, new Win32Exception(Marshal.GetLastPInvokeError()));

    public void Dispose() => handle.Dispose();

    [StructLayout(LayoutKind.Sequential)]
    private struct AttributeTagInfo
    {
        internal FileAttributes Attributes;
        internal uint ReparseTag;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ObjectAttributes
    {
        internal int Length;
        internal nint RootDirectory;
        internal nint ObjectName;
        internal uint Attributes;
        internal nint SecurityDescriptor;
        internal nint SecurityQualityOfService;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct UnicodeString
    {
        internal ushort Length;
        internal ushort MaximumLength;
        internal nint Buffer;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoStatusBlock
    {
        internal nint Status;
        internal nint Information;
    }

    [DllImport("kernel32.dll", EntryPoint = "CreateFileW", CharSet = CharSet.Unicode, SetLastError = true, ExactSpelling = true)]
    private static extern SafeFileHandle CreateFile(string path, uint access, FileShare share, nint security,
        FileMode disposition, uint flags, nint template);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandleEx(SafeFileHandle file, int infoClass, nint info, int size);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandleEx(SafeFileHandle file, int infoClass, out AttributeTagInfo info, int size);

    [DllImport("ntdll.dll")]
    private static extern int NtCreateFile(out SafeFileHandle file, uint access, ref ObjectAttributes attributes,
        out IoStatusBlock status, nint allocationSize, uint fileAttributes, FileShare share, uint disposition, uint options,
        nint extendedAttributes, uint extendedAttributesLength);

    [DllImport("ntdll.dll")]
    private static extern uint RtlNtStatusToDosError(int status);
}
