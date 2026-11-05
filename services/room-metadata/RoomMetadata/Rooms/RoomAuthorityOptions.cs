using System.Net;

namespace SyncWatch.RoomMetadata.Rooms;

/// <summary>Deployment-owned TLS listener inputs; never accepted from a room request.</summary>
public sealed record RoomAuthorityOptions(string Runtime, string TlsDirectory, string BindAddress, string PublicHost, int Port)
{
    public static RoomAuthorityOptions? FromEnvironment()
    {
        string? runtime = Environment.GetEnvironmentVariable("SYNCWATCH_SYNCPLAY_SERVER_RUNTIME");
        string? tls = Environment.GetEnvironmentVariable("SYNCWATCH_SYNCPLAY_TLS_DIRECTORY");
        string? bind = Environment.GetEnvironmentVariable("SYNCWATCH_SYNCPLAY_BIND_ADDRESS");
        string? host = Environment.GetEnvironmentVariable("SYNCWATCH_SYNCPLAY_PUBLIC_HOST");
        string? port = Environment.GetEnvironmentVariable("SYNCWATCH_SYNCPLAY_PORT");
        if (runtime is null && tls is null && bind is null && host is null && port is null) return null;
        Wire.Need(runtime is not null && tls is not null && bind is not null && host is not null && port is not null,
            "roomAuthorityConfigurationRequired", 503);
        Wire.Need(int.TryParse(port, System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture,
            out int number), "roomAuthorityConfigurationInvalid", 503);
        var options = new RoomAuthorityOptions(runtime!, tls!, bind!, host!, number);
        options.Validate();
        return options;
    }

    public void Validate()
    {
        Wire.Need(Path.IsPathFullyQualified(Runtime) && Path.IsPathFullyQualified(TlsDirectory)
            && IPAddress.TryParse(BindAddress, out _) && Wire.Text(PublicHost, 253)
            && Uri.CheckHostName(PublicHost) != UriHostNameType.Unknown && Port is >= 1 and <= 65535,
            "roomAuthorityConfigurationInvalid", 503);
    }
}
