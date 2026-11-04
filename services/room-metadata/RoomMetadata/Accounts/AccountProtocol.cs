using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace SyncWatch.RoomMetadata.Accounts;

public sealed class AccountException(string code, int status = 400) : Exception(code)
{
    public string Code { get; } = code;
    public int Status { get; } = status;
}

public sealed record AccountProfile(string Id, string Name, string Email);
public sealed record DeviceSession(AccountProfile Account, string AccessToken, string RefreshToken, int ExpiresIn = 600)
{
    public override string ToString() => "[account session redacted]";
}

internal static partial class AccountWire
{
    internal const int Version = 2;
    internal static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web) { UnmappedMemberHandling = System.Text.Json.Serialization.JsonUnmappedMemberHandling.Disallow };
    internal static void Need([System.Diagnostics.CodeAnalysis.DoesNotReturnIf(false)] bool condition, string code = "malformed", int status = 400)
    {
        if (!condition) throw new AccountException(code, status);
    }
    internal static string Secret() => Base64(RandomNumberGenerator.GetBytes(32));
    internal static string Base64(byte[] bytes) => Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    internal static string Hash(string value) => Base64(SHA256.HashData(Encoding.ASCII.GetBytes(value)));
    internal static bool Token(string? value) => value is not null && TokenPattern().IsMatch(value);
    internal static bool Matches(string value, string hash) => Token(value) && Token(hash)
        && CryptographicOperations.FixedTimeEquals(Encoding.ASCII.GetBytes(Hash(value)), Encoding.ASCII.GetBytes(hash));
    internal static string BrowserProof() => Convert.ToHexString(RandomNumberGenerator.GetBytes(6));
    internal static bool BrowserProofMatches(string? value, string? hash) => value is { Length: 12 }
        && value.All(character => char.IsAsciiHexDigitUpper(character)) && Token(hash)
        && CryptographicOperations.FixedTimeEquals(Encoding.ASCII.GetBytes(Hash(value)), Encoding.ASCII.GetBytes(hash!));
    internal static bool Text(string? value, int maximum) => value is { Length: > 0 } && value.Length <= maximum && !value.Any(char.IsControl);
    [GeneratedRegex("^[A-Za-z0-9_-]{43}$", RegexOptions.CultureInvariant)] private static partial Regex TokenPattern();
}
