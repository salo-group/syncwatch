using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace SyncWatch.Room;

public sealed record AccountProfile(string Id, string Name, string Email);
public sealed class AccountDeviceSession
{
    public AccountProfile Account { get; }
    internal string Access { get; }
    internal string Refresh { get; }
    internal AccountDeviceSession(AccountProfile account, string access, string refresh) { Account = account; Access = access; Refresh = refresh; }
    public override string ToString() => "[account session redacted]";
}
public sealed class AccountSignIn
{
    public Uri BrowserUri { get; }
    internal string Id { get; }
    internal string Proof { get; }
    internal string Confirmation { get; } = AccountClient.Secret();
    internal AccountSignIn(Uri browserUri, string id, string proof) { BrowserUri = browserUri; Id = id; Proof = proof; }
    public override string ToString() => "[account sign-in redacted]";
}
public sealed record AccountSignInStatus(string State, AccountProfile? Account);

/// <summary>Independent account API; no Google token is sent to a desktop.</summary>
public sealed class AccountClient(RoomAddress address) : IDisposable
{
    private readonly HttpClient client = new(new SocketsHttpHandler { AllowAutoRedirect = false, UseCookies = false, ConnectTimeout = TimeSpan.FromSeconds(10) }) { Timeout = TimeSpan.FromSeconds(20) };
    public async Task<AccountSignIn> StartAsync(CancellationToken cancellation = default)
    {
        string proof = Secret(), challenge = Base64(SHA256.HashData(Encoding.ASCII.GetBytes(proof)));
        JsonElement result = await Request("sign-ins", new { v = 2, challenge }, cancellation: cancellation);
        string id = Token(result.GetProperty("transactionId"));
        Uri uri = new(address.Origin, "auth/google/start?id=" + id);
        Need(result.GetProperty("browserUri").GetString() == uri.AbsoluteUri && result.GetProperty("expiresIn").GetInt32() == 300
            && result.GetProperty("pollAfterMs").GetInt32() == 2000);
        return new(uri, id, proof);
    }
    public async Task<AccountSignInStatus> PollAsync(AccountSignIn signIn, CancellationToken cancellation = default)
    {
        JsonElement result = await Request("sign-ins/" + signIn.Id + "/poll", new { v = 2, proof = signIn.Proof }, cancellation: cancellation);
        string state = result.GetProperty("status").GetString()!;
        Need(state is "pending" or "authorizing" or "browserConfirmationRequired" or "confirmAccount" or "complete" or "cancelled" or "failed");
        return new(state, state == "confirmAccount" ? Profile(result.GetProperty("account"), proposal: true) : null);
    }
    public async Task<AccountProfile> VerifyBrowserAsync(AccountSignIn signIn, string browserProof, CancellationToken cancellation = default)
    {
        string code = browserProof.Replace(" ", "").Replace("-", "").ToUpperInvariant();
        Need(code.Length == 12 && code.All(char.IsAsciiHexDigitUpper));
        JsonElement result = await Request("sign-ins/" + signIn.Id + "/verify", new { v = 2, proof = signIn.Proof, browserProof = code }, cancellation: cancellation);
        Need(result.GetProperty("status").GetString() == "confirmAccount");
        return Profile(result.GetProperty("account"), proposal: true);
    }
    public async Task<AccountDeviceSession> ConfirmAsync(AccountSignIn signIn, string browserProof, CancellationToken cancellation = default)
    {
        string code = browserProof.Replace(" ", "").Replace("-", "").ToUpperInvariant();
        Need(code.Length == 12 && code.All(char.IsAsciiHexDigitUpper));
        JsonElement result = await Request("sign-ins/" + signIn.Id + "/confirm", new { v = 2, proof = signIn.Proof, requestId = signIn.Confirmation, browserProof = code }, cancellation: cancellation);
        Need(result.GetProperty("status").GetString() == "complete");
        return Device(result.GetProperty("session"));
    }
    public async Task CancelAsync(AccountSignIn signIn, CancellationToken cancellation = default) =>
        _ = await Request("sign-ins/" + signIn.Id + "/cancel", new { v = 2, proof = signIn.Proof }, cancellation: cancellation);
    public AccountSavedSession Checkpoint(AccountDeviceSession session) => new(address.Origin.AbsoluteUri, session.Account.Id, session.Refresh, Secret());
    public Task<AccountDeviceSession> RefreshAsync(AccountDeviceSession session, CancellationToken cancellation = default) => ResumeAsync(Checkpoint(session), cancellation);
    public async Task<AccountDeviceSession> ResumeAsync(AccountSavedSession saved, CancellationToken cancellation = default)
    {
        Need(new RoomAddress(saved.Service).Origin == address.Origin);
        var body = new { v = 2, refreshToken = saved.RefreshToken, requestId = saved.RequestId };
        AccountDeviceSession result;
        try { result = Device((await Request("refresh", body, cancellation: cancellation)).GetProperty("session")); }
        catch (Exception error) when (error is HttpRequestException || error is OperationCanceledException && !cancellation.IsCancellationRequested)
        { result = Device((await Request("refresh", body, cancellation: cancellation)).GetProperty("session")); }
        if (result.Account.Id != saved.AccountId) { await LogoutAsync(result); throw new RoomProtocolException("malformed"); }
        return result;
    }
    public async Task LogoutAsync(AccountDeviceSession session, CancellationToken cancellation = default) =>
        _ = await Request("logout", bearer: session.Refresh, cancellation: cancellation);

    private async Task<JsonElement> Request(string path, object? body = null, string? bearer = null, CancellationToken cancellation = default)
    {
        using HttpRequestMessage request = new(HttpMethod.Post, new Uri(address.Origin, "v2/account/" + path));
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        if (bearer is not null) request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", bearer);
        if (body is not null) request.Content = new ByteArrayContent(JsonSerializer.SerializeToUtf8Bytes(body)) { Headers = { ContentType = new MediaTypeHeaderValue("application/json") } };
        using HttpResponseMessage response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellation);
        Need(response.Content.Headers.ContentLength is not > 16384 && response.Content.Headers.ContentType?.MediaType == "application/json");
        using Stream stream = await response.Content.ReadAsStreamAsync(cancellation);
        byte[] bytes = new byte[16385];
        int count = await stream.ReadAtLeastAsync(bytes, bytes.Length, throwOnEndOfStream: false, cancellation);
        Need(count <= 16384);
        using JsonDocument json = RoomWire.Parse(bytes.AsMemory(0, count));
        Need(json.RootElement.GetProperty("v").GetInt32() == 2);
        if (!response.IsSuccessStatusCode) throw new RoomProtocolException(response.StatusCode == System.Net.HttpStatusCode.Unauthorized ? "accountSignInRequired" : "accountUnavailable");
        return json.RootElement.GetProperty("result").Clone();
    }
    private static AccountDeviceSession Device(JsonElement value)
    {
        Need(value.GetProperty("expiresIn").GetInt32() == 600);
        return new(Profile(value.GetProperty("account")), Token(value.GetProperty("accessToken")), Token(value.GetProperty("refreshToken")));
    }
    private static AccountProfile Profile(JsonElement value, bool proposal = false)
    {
        string id = value.GetProperty("id").GetString()!, name = value.GetProperty("name").GetString()!, email = value.GetProperty("email").GetString()!;
        Need((proposal ? id == "" : IsToken(id)) && name is { Length: > 0 and <= 128 } && email is { Length: > 0 and <= 320 }
            && !name.Concat(email).Any(char.IsControl));
        return new(id, name, email);
    }
    private static string Token(JsonElement value) { string text = value.GetString()!; Need(IsToken(text)); return text; }
    private static bool IsToken(string? value) => value is { Length: 43 } && value.All(c => char.IsAsciiLetterOrDigit(c) || c is '_' or '-');
    private static void Need(bool value) { if (!value) throw new RoomProtocolException("malformed"); }
    private static string Base64(byte[] bytes) => Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    internal static string Secret() => Base64(RandomNumberGenerator.GetBytes(32));
    public void Dispose() => client.Dispose();
}
