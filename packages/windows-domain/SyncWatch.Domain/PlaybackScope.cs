namespace SyncWatch.Domain;

public enum ContractRejection
{
    Malformed,
    ForeignRun,
    StaleGeneration
}

public sealed class ContractException(ContractRejection rejection) : Exception(rejection.ToString())
{
    public ContractRejection Rejection { get; } = rejection;
}

/// <summary>Trusted local media ownership, independent of WPF and playback engines.</summary>
public sealed record PlaybackScope
{
    public const ulong MaximumGeneration = 9_007_199_254_740_991;
    public string ClientRunId { get; }
    public ulong MediaGeneration { get; }

    public PlaybackScope(string clientRunId, ulong mediaGeneration)
    {
        if (string.IsNullOrEmpty(clientRunId) || clientRunId.Length > 64
            || !char.IsAsciiLetterOrDigit(clientRunId[0])
            || clientRunId.Any(character => !char.IsAsciiLetterOrDigit(character) && character is not ('-' or '.' or '_'))
            || mediaGeneration > MaximumGeneration)
        {
            throw new ContractException(ContractRejection.Malformed);
        }

        ClientRunId = clientRunId;
        MediaGeneration = mediaGeneration;
    }

    public static PlaybackScope Create() => new(Guid.NewGuid().ToString("D"), 0);

    public void Check(string clientRunId, ulong mediaGeneration)
    {
        PlaybackScope incoming = new(clientRunId, mediaGeneration);
        if (incoming.ClientRunId != ClientRunId)
        {
            throw new ContractException(ContractRejection.ForeignRun);
        }

        if (incoming.MediaGeneration != MediaGeneration)
        {
            throw new ContractException(ContractRejection.StaleGeneration);
        }
    }

    public PlaybackScope ReplacingMedia()
    {
        if (MediaGeneration >= MaximumGeneration)
        {
            throw new ContractException(ContractRejection.Malformed);
        }

        return new PlaybackScope(ClientRunId, MediaGeneration + 1);
    }
}
