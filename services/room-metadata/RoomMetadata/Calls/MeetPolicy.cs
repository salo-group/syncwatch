using System.Text.RegularExpressions;

namespace SyncWatch.RoomMetadata.Calls;

internal static partial class MeetPolicy
{
    internal const string CreatedScope = "https://www.googleapis.com/auth/meetings.space.created";
    internal const int MaximumCredentialLength = 4096;
    internal const int MaximumGrants = 10000;
    internal const int MaximumCalls = 4096;
    internal const int CallWorkers = 4;
    internal const int MaximumQueuedCalls = 32;
    internal const int MaximumStoreBytes = 16 * 1024 * 1024;
    internal static readonly TimeSpan AuthorizationLifetime = TimeSpan.FromMinutes(5);
    internal static readonly TimeSpan ProviderTimeout = TimeSpan.FromSeconds(10);
    internal static readonly TimeSpan AccessRenewalMargin = TimeSpan.FromMinutes(1);
    internal const int AuthorizationPollMilliseconds = 2000;
    internal const int CallPollMilliseconds = 5000;
    internal static bool Credential(string? value) => value is { Length: > 0 and <= MaximumCredentialLength }
        && value.All(character => character is >= '!' and <= '~');
    internal static bool SpaceName(string? value) => value is not null && SpacePattern().IsMatch(value);
    internal static bool ConferenceName(string? value) => value is not null && ConferencePattern().IsMatch(value);
    internal static bool MeetUri(string? value) => value is not null && MeetUriPattern().IsMatch(value);
    [GeneratedRegex("\\Aspaces/[A-Za-z0-9_-]{1,128}\\z", RegexOptions.CultureInvariant)] private static partial Regex SpacePattern();
    [GeneratedRegex("\\AconferenceRecords/[A-Za-z0-9_-]{1,128}\\z", RegexOptions.CultureInvariant)] private static partial Regex ConferencePattern();
    [GeneratedRegex("\\Ahttps://meet\\.google\\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}\\z", RegexOptions.CultureInvariant)] private static partial Regex MeetUriPattern();
}
