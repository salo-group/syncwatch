using System.Net.Http;
using SyncWatch.Room;

namespace SyncWatch;

internal sealed class RoomAdmissionException(string notice) : Exception
{
    internal string Notice { get; } = notice;
}

/// <summary>One correlated account attempt, including safe retries and unknown-accept cleanup.</summary>
internal sealed class AccountRoomAdmission
{
    private readonly RoomClient client;
    private readonly bool owner;
    private readonly RoomInvitation? invitation;
    private readonly string run = "run-" + Guid.NewGuid().ToString("N"), requestId = "request-" + Guid.NewGuid().ToString("N");
    private RoomAdmissionAttempt? attempt;
    internal AccountRoomAdmission(RoomClient client, bool owner, IReadOnlyDictionary<string, string> values)
    {
        this.client = client; this.owner = owner;
        if (!owner) invitation = new(values["roomID"], new(values["invitation"]));
    }
    internal async Task<RoomAdmission> PerformAsync(Action waiting, CancellationToken cancellation)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        deadline.CancelAfter(TimeSpan.FromMinutes(2));
        try
        {
            if (owner) return await Retry(() => client.CreateForAccountAsync(run, requestId, deadline.Token), deadline.Token);
            var request = await Retry(() => client.RequestAdmissionAsync(invitation!, run, requestId, deadline.Token), deadline.Token);
            attempt = request; deadline.Token.ThrowIfCancellationRequested(); waiting();
            string status = request.Request.Status;
            while (true)
            {
                if (status is "approved" or "admitted") return await Retry(() => client.AcceptAsync(request, deadline.Token), deadline.Token);
                if (status == "denied") throw new RoomAdmissionException("roomAdmissionDenied");
                if (status != "pending") throw new RoomAdmissionException("roomAdmissionExpired");
                await Task.Delay(TimeSpan.FromSeconds(3), deadline.Token);
                status = (await Retry(() => client.AdmissionStatusAsync(request, deadline.Token), deadline.Token)).Status;
            }
        }
        catch (OperationCanceledException) when (!cancellation.IsCancellationRequested && deadline.IsCancellationRequested)
        { throw new RoomAdmissionException("roomAdmissionExpired"); }
    }
    internal Task CancelAsync(CancellationToken cancellation) => attempt is null ? Task.CompletedTask : client.CancelAdmissionAsync(attempt, cancellation);
    private static async Task<T> Retry<T>(Func<Task<T>> operation, CancellationToken cancellation)
    {
        for (int index = 0; ; index++)
        {
            try { return await operation(); }
            catch (Exception error) when (index == 0 && Transient(error))
            {
                cancellation.ThrowIfCancellationRequested();
                await Task.Delay(TimeSpan.FromSeconds(1), cancellation);
            }
        }
    }
    private static bool Transient(Exception error) => error is RoomProtocolException { Message: "transport" }
        || error is OperationCanceledException
        || error is HttpRequestException { HttpRequestError: HttpRequestError.ConnectionError or HttpRequestError.NameResolutionError or HttpRequestError.ResponseEnded };
}
