using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;
using SyncWatch.RoomMetadata.Accounts;

namespace SyncWatch.RoomMetadata.Calls;

internal sealed class MeetProviderException(string code, bool ambiguous = false, string? grantVersion = null) : Exception(code)
{
    internal string Code { get; } = code;
    internal bool Ambiguous { get; } = ambiguous;
    internal string? GrantVersion { get; } = grantVersion;
}
internal sealed record MeetSpace(string Name, string MeetUri, string? Conference)
{
    public override string ToString() => "[Google Meet space redacted]";
}

/// <summary>Confidential Google transport. Mutations never retry; callers journal dispatch before invoking them.</summary>
internal sealed class MeetGoogleProvider : IMeetProvider, IDisposable
{
    private sealed record Access(string Version, string Token, DateTimeOffset Until, bool? ManagedDomain)
    { public override string ToString() => "[provider access redacted]"; }
    private readonly MeetGrantRepository grants;
    private readonly AccountOptions options;
    private readonly HttpClient http;
    private readonly TimeProvider clock;
    private readonly Action<string, int, string> reportFailure;
    // Stripes bound synchronization memory independently of the number of provider requests.
    private readonly SemaphoreSlim[] gates = Enumerable.Range(0, 32).Select(_ => new SemaphoreSlim(1, 1)).ToArray();
    private readonly object cacheGate = new();
    private readonly Dictionary<string, Access> cache = new(StringComparer.Ordinal);
    internal MeetGoogleProvider(MeetGrantRepository grants, AccountOptions options, HttpClient? http = null, TimeProvider? time = null,
        Action<string, int, string>? reportFailure = null)
    {
        this.grants = grants; this.options = options; clock = time ?? TimeProvider.System;
        this.reportFailure = reportFailure ?? ((operation, status, reason) => Console.Error.WriteLine($"Meet provider {operation} rejected (HTTP {status}, {reason})."));
        this.http = http ?? new HttpClient(new HttpClientHandler { AllowAutoRedirect = false, UseCookies = false });
        this.http.Timeout = MeetPolicy.ProviderTimeout;
    }
    public async Task<MeetSpace> Create(string account, CancellationToken cancel)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, "https://meet.googleapis.com/v2/spaces");
        using var response = await Send(account, request, mutation: true, cancel, createSpace: true);
        try { return Space(response.RootElement, requirePolicy: false); }
        catch (MeetProviderException error) { throw new MeetProviderException(error.Code, true); }
    }
    public async Task<MeetSpace> Read(string account, string name, CancellationToken cancel)
    {
        NeedSpace(name);
        var grant = grants.ReadForProvider(account);
        using var request = new HttpRequestMessage(HttpMethod.Get, "https://meet.googleapis.com/v2/" + name);
        using var response = await Send(account, request, mutation: false, cancel);
        bool personal = grant is { ManagedDomain: false } && grants.IsCurrent(grant);
        MeetSpace result = Space(response.RootElement, requirePolicy: true, personal);
        if (result.Name != name) throw new MeetProviderException("providerResponseInvalid");
        return result;
    }
    public async Task End(string account, string name, CancellationToken cancel)
    {
        NeedSpace(name);
        using var request = new HttpRequestMessage(HttpMethod.Post, "https://meet.googleapis.com/v2/" + name + ":endActiveConference")
        { Content = JsonContent.Create(new { }) };
        using var response = await Send(account, request, mutation: true, cancel);
        if (response.RootElement.ValueKind != JsonValueKind.Object) throw new MeetProviderException("providerResponseInvalid", true);
    }
    private async Task<JsonDocument> Send(string account, HttpRequestMessage request, bool mutation, CancellationToken cancel, bool createSpace = false)
    {
        // A token failure occurs before provider dispatch and cannot have created a space.
        var access = await Credential(account, cancel);
        // Personal accounts cannot set RESTRICTED. Google selects their access configuration;
        // readback verifies account-appropriate admission before publishing any URI. Unknown legacy grants
        // and managed accounts retain an explicit policy request. No mutation is retried or downgraded.
        if (createSpace) request.Content = access.ManagedDomain is false ? JsonContent.Create(new { })
            : JsonContent.Create(new { config = new { accessType = "RESTRICTED", entryPointAccess = "ALL" } });
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", access.Token);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        cancel.ThrowIfCancellationRequested();
        if (!Current(account, access)) throw new MeetProviderException("permissionRequired", grantVersion: access.Version);
        try
        {
            using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancel);
            if (!response.IsSuccessStatusCode)
            {
                if (response.StatusCode == HttpStatusCode.Unauthorized) Invalidate(account, access);
                string operation = request.Method == HttpMethod.Get ? "read"
                    : request.RequestUri!.AbsolutePath.EndsWith(":endActiveConference", StringComparison.Ordinal) ? "end" : "create";
                string reason = await FailureReason(response, cancel);
                try { reportFailure(operation, (int)response.StatusCode, reason); }
                catch (Exception) { } // Diagnostics cannot change a known provider rejection into an unknown mutation.
                string code = response.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden
                    ? "permissionRequired" : response.StatusCode == HttpStatusCode.TooManyRequests ? "providerBusy" : "providerUnavailable";
                throw new MeetProviderException(code,
                    mutation && ((int)response.StatusCode >= 500 || (int)response.StatusCode is >= 300 and < 400),
                    code == "permissionRequired" ? access.Version : null);
            }
            JsonDocument result;
            try { result = await ReadJson(response, cancel); }
            catch (MeetProviderException error) { throw new MeetProviderException(error.Code, mutation); }
            // Forget/reauthorize during I/O cannot publish a result through the preceding grant.
            if (!Current(account, access)) { result.Dispose(); throw new MeetProviderException("permissionRequired", mutation, access.Version); }
            return result;
        }
        catch (MeetProviderException) { throw; }
        catch (Exception error) when (error is HttpRequestException or IOException or OperationCanceledException or JsonException)
        { throw new MeetProviderException("providerUnavailable", mutation); }
    }
    private async Task<Access> Credential(string account, CancellationToken cancel)
    {
        var semaphore = gates[(int)((uint)StringComparer.Ordinal.GetHashCode(account) % (uint)gates.Length)];
        await semaphore.WaitAsync(cancel);
        try
        {
            var grant = grants.ReadForProvider(account) ?? throw new MeetProviderException("permissionRequired");
            lock (cacheGate)
            {
                if (cache.TryGetValue(account, out var existing) && existing.Version == grant.Version
                    && existing.Until - MeetPolicy.AccessRenewalMargin > clock.GetUtcNow()) return existing;
                cache.Remove(account);
            }
            using var request = new HttpRequestMessage(HttpMethod.Post, "https://oauth2.googleapis.com/token")
            {
                Content = new FormUrlEncodedContent(new Dictionary<string, string>
                { ["client_id"] = options.ClientId, ["client_secret"] = options.ClientSecret, ["refresh_token"] = grant.RefreshToken, ["grant_type"] = "refresh_token" })
            };
            try
            {
                using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancel);
                if (!response.IsSuccessStatusCode)
                {
                    // Do not delete a refresh grant on transient provider/network errors.
                    if (response.StatusCode == HttpStatusCode.BadRequest)
                    {
                        using var failure = await ReadJson(response, cancel);
                        if (Text(failure.RootElement, "error") == "invalid_grant") grants.ForgetCurrent(grant);
                    }
                    throw new MeetProviderException(response.StatusCode is HttpStatusCode.BadRequest or HttpStatusCode.Unauthorized
                        ? "permissionRequired" : "providerUnavailable");
                }
                using var json = await ReadJson(response, cancel); var value = json.RootElement;
                string token = Text(value, "access_token");
                if (!MeetPolicy.Credential(token) || !string.Equals(Text(value, "token_type"), "Bearer", StringComparison.OrdinalIgnoreCase)
                    || !value.TryGetProperty("expires_in", out var lifetime) || lifetime.ValueKind != JsonValueKind.Number
                    || !lifetime.TryGetInt32(out int seconds) || seconds is < 120 or > 86400
                    || value.TryGetProperty("scope", out var scope) && (scope.ValueKind != JsonValueKind.String
                        || !(scope.GetString() ?? "").Split(' ', StringSplitOptions.RemoveEmptyEntries).Contains(MeetPolicy.CreatedScope, StringComparer.Ordinal)))
                    throw new MeetProviderException("providerResponseInvalid");
                if (value.TryGetProperty("refresh_token", out var rotated))
                {
                    if (rotated.ValueKind != JsonValueKind.String || !MeetPolicy.Credential(rotated.GetString())) throw new MeetProviderException("providerResponseInvalid");
                    grant = grants.ReplaceRefresh(grant, rotated.GetString()!) ?? throw new MeetProviderException("permissionRequired");
                }
                if (!grants.IsCurrent(grant)) throw new MeetProviderException("permissionRequired");
                Access access = new(grant.Version, token, clock.GetUtcNow().AddSeconds(seconds), grant.ManagedDomain);
                lock (cacheGate)
                {
                    foreach (string key in cache.Where(item => item.Value.Until <= clock.GetUtcNow()).Select(item => item.Key).ToArray()) cache.Remove(key);
                    if (cache.Count < MeetPolicy.MaximumGrants || cache.ContainsKey(account)) cache[account] = access;
                }
                return access;
            }
            catch (MeetProviderException) { throw; }
            catch (Exception error) when (error is HttpRequestException or IOException or OperationCanceledException or JsonException)
            { throw new MeetProviderException("providerUnavailable"); }
        }
        finally { semaphore.Release(); }
    }
    private bool Current(string account, Access access) => grants.ReadForProvider(account) is { } grant && grant.Version == access.Version;
    private void Invalidate(string account, Access access)
    { lock (cacheGate) { if (cache.TryGetValue(account, out var current) && current == access) cache.Remove(account); } }
    private static MeetSpace Space(JsonElement value, bool requirePolicy, bool personal = false)
    {
        string name = Text(value, "name"), uri = Text(value, "meetingUri"); NeedSpace(name);
        if (!MeetPolicy.MeetUri(uri)) throw new MeetProviderException("providerResponseInvalid");
        if (requirePolicy)
        {
            if (!value.TryGetProperty("config", out var config) || Text(config, "entryPointAccess") != "ALL")
                throw new MeetProviderException("providerPolicyInvalid");
            string accessType = Text(config, "accessType");
            // Without a hosted domain or telephone/SIP entry, TRUSTED admits only invitees without
            // knocking, like RESTRICTED. Never extend this to a managed or unclassified account.
            if (accessType != "RESTRICTED" && !(personal && accessType == "TRUSTED"
                && NoEntries(value, "phoneAccess") && NoEntries(value, "gatewaySipAccess")))
                throw new MeetProviderException("providerPolicyInvalid");
        }
        string? conference = null;
        if (value.TryGetProperty("activeConference", out var active))
        {
            conference = Text(active, "conferenceRecord");
            if (!MeetPolicy.ConferenceName(conference)) throw new MeetProviderException("providerResponseInvalid");
        }
        return new(name, uri, conference);
    }
    private static bool NoEntries(JsonElement value, string key) => !value.TryGetProperty(key, out var entries)
        || entries.ValueKind == JsonValueKind.Array && entries.GetArrayLength() == 0;
    private static string Text(JsonElement value, string name) => value.ValueKind == JsonValueKind.Object
        && value.TryGetProperty(name, out var property) && property.ValueKind == JsonValueKind.String
            ? property.GetString()! : throw new MeetProviderException("providerResponseInvalid");
    private static void NeedSpace(string name) { if (!MeetPolicy.SpaceName(name)) throw new MeetProviderException("providerResponseInvalid"); }
    private static async Task<string> FailureReason(HttpResponseMessage response, CancellationToken cancel)
    {
        // Only fixed Google ErrorInfo reason values leave the transport. Never emit bodies, messages,
        // resource names, project/account identifiers, HTTP headers or provider credentials.
        string[] allowed = ["SERVICE_DISABLED", "SERVICE_NOT_ACTIVATED", "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
            "USER_PROJECT_DENIED", "CONSUMER_INVALID", "IAM_PERMISSION_DENIED", "ACCOUNT_RESTRICTED", "ORG_RESTRICTION_VIOLATION"];
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancel);
        deadline.CancelAfter(MeetPolicy.ProviderTimeout);
        try
        {
            using var body = await ReadJson(response, deadline.Token);
            if (body.RootElement.ValueKind == JsonValueKind.Object && body.RootElement.TryGetProperty("error", out var error)
                && error.ValueKind == JsonValueKind.Object && error.TryGetProperty("details", out var details) && details.ValueKind == JsonValueKind.Array)
                foreach (var detail in details.EnumerateArray())
                    if (detail.ValueKind == JsonValueKind.Object && detail.TryGetProperty("reason", out var reason)
                        && reason.ValueKind == JsonValueKind.String && allowed.Contains(reason.GetString(), StringComparer.Ordinal)) return reason.GetString()!;
        }
        catch (Exception error) when (error is MeetProviderException or HttpRequestException or IOException or OperationCanceledException or JsonException) { }
        return "unspecified";
    }
    private static async Task<JsonDocument> ReadJson(HttpResponseMessage response, CancellationToken cancel)
    {
        const int maximum = 32768;
        if (response.Content.Headers.ContentLength > maximum || response.Content.Headers.ContentType?.MediaType != "application/json")
            throw new MeetProviderException("providerResponseInvalid");
        await using var stream = await response.Content.ReadAsStreamAsync(cancel);
        using var bytes = new MemoryStream(); byte[] buffer = new byte[4096];
        while (true)
        {
            int read = await stream.ReadAsync(buffer, cancel); if (read == 0) break;
            if (bytes.Length + read > maximum) throw new MeetProviderException("providerResponseInvalid");
            bytes.Write(buffer, 0, read);
        }
        JsonDocument json = JsonDocument.Parse(bytes.ToArray(), new JsonDocumentOptions { MaxDepth = 16 });
        static bool Unique(JsonElement element) => element.ValueKind switch
        {
            JsonValueKind.Object => element.EnumerateObject().Select(item => item.Name).Distinct(StringComparer.Ordinal).Count() == element.EnumerateObject().Count()
                && element.EnumerateObject().All(item => Unique(item.Value)),
            JsonValueKind.Array => element.EnumerateArray().All(Unique),
            _ => true
        };
        if (!Unique(json.RootElement)) { json.Dispose(); throw new MeetProviderException("providerResponseInvalid"); }
        return json;
    }
    public void Dispose() { http.Dispose(); foreach (var semaphore in gates) semaphore.Dispose(); lock (cacheGate) cache.Clear(); }
}
