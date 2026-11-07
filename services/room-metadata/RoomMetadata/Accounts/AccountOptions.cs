using System.Text.Json;

namespace SyncWatch.RoomMetadata.Accounts;

/// <summary>Confidential server configuration; never supplied by desktop clients.</summary>
public sealed record AccountOptions(string ClientId, string ClientSecret, Uri PublicOrigin, string Directory)
{
    public override string ToString() => "[confidential account configuration redacted]";
    public static AccountOptions? FromEnvironment()
    {
        string? file = Environment.GetEnvironmentVariable("SYNCWATCH_GOOGLE_CLIENT_FILE");
        string? origin = Environment.GetEnvironmentVariable("SYNCWATCH_PUBLIC_ORIGIN");
        string? directory = Environment.GetEnvironmentVariable("SYNCWATCH_ACCOUNT_DIRECTORY");
        if (file is null && origin is null && directory is null) return null;
        AccountWire.Need(file is not null && origin is not null && directory is not null, "accountConfigurationRequired");
        try
        {
            FileInfo info = new(file!);
            AccountWire.Need(info.Exists && info.Length <= 16384 && info.LinkTarget is null, "accountConfigurationInvalid");
            using JsonDocument json = JsonDocument.Parse(File.ReadAllBytes(file!));
            JsonElement web = json.RootElement.GetProperty("web");
            Uri publicOrigin = new(origin!, UriKind.Absolute);
            AccountWire.Need(publicOrigin.AbsolutePath == "/" && publicOrigin.Query == "" && publicOrigin.Fragment == ""
                && publicOrigin.UserInfo == "" && (publicOrigin.Scheme == "https"
                    || publicOrigin.Scheme == "http" && publicOrigin.Host == "127.0.0.1"), "accountOriginInvalid");
            string callback = new Uri(publicOrigin, "/auth/google/callback").AbsoluteUri;
            AccountWire.Need(web.GetProperty("redirect_uris").EnumerateArray().Any(value => value.GetString() == callback), "accountCallbackInvalid");
            string id = web.GetProperty("client_id").GetString()!, secret = web.GetProperty("client_secret").GetString()!;
            AccountWire.Need(AccountWire.Text(id, 256) && AccountWire.Text(secret, 256), "accountConfigurationInvalid");
            return new(id, secret, publicOrigin, Path.GetFullPath(directory!));
        }
        catch (AccountException) { throw; }
        catch (Exception error) when (error is IOException or JsonException or InvalidOperationException or KeyNotFoundException or UriFormatException)
        { throw new AccountException("accountConfigurationInvalid"); }
    }
}
