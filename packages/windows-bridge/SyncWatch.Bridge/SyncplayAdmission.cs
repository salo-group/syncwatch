using SyncWatch.Domain;

namespace SyncWatch.Bridge;

/// <summary>Ephemeral authority for one transport, with redacted diagnostics.</summary>
public sealed class SyncplayAdmission
{
    internal string Credential { get; }
    internal string ClientRunId { get; }
    public SyncplayAdmission(string credential, string clientRunId)
    {
        if (credential.Length != 64 || credential.Any(c => c is not (>= '0' and <= '9' or >= 'A' and <= 'F'))) throw new PlayerCorrectionException("malformed");
        _ = new PlaybackScope(clientRunId, 0); Credential = credential; ClientRunId = clientRunId;
    }
    public override string ToString() => "[redacted]";
}
