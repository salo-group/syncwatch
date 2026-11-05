using System.Net.WebSockets;

namespace SyncWatch.RoomMetadata;

/// <summary>One bounded ordered-event transport for each room authorization policy.</summary>
internal static class RoomEventTransport
{
    internal static async Task Run(HttpContext context, Func<string?, long?, RoomStream> attach,
        Func<byte[], CommandResult> execute, Action<RoomStream> detach)
    {
        Wire.Need(context.WebSockets.IsWebSocketRequest, "webSocketRequired", 400);
        Wire.Need(context.Request.Query.All(item => item.Key is "epoch" or "cursor" && item.Value.Count == 1));
        string? epoch = context.Request.Query.ContainsKey("epoch") ? context.Request.Query["epoch"].ToString() : null;
        long? cursor = null;
        if (context.Request.Query.ContainsKey("cursor"))
        {
            Wire.Need(long.TryParse(context.Request.Query["cursor"], System.Globalization.NumberStyles.None,
                System.Globalization.CultureInfo.InvariantCulture, out long parsed) && parsed <= Wire.MaximumCounter);
            cursor = parsed;
        }
        using RoomStream stream = attach(epoch, cursor);
        try
        {
            using WebSocket socket = await context.WebSockets.AcceptWebSocketAsync();
            using CancellationTokenSource stop = CancellationTokenSource.CreateLinkedTokenSource(context.RequestAborted, stream.Stopped.Token,
                context.RequestServices.GetRequiredService<IHostApplicationLifetime>().ApplicationStopping);
            Task sender = Send(socket, stream, stop.Token);
            try
            {
                while (!stop.IsCancellationRequested)
                {
                    byte[]? bytes = await Receive(socket, stop.Token);
                    if (bytes is null) { break; }
                    try { stream.Enqueue(execute(bytes)); }
                    catch (RoomException error) { stream.Enqueue(new { v = 1, type = "room.error", error = new { code = error.Code } }); }
                }
            }
            catch (Exception error) when (error is OperationCanceledException or WebSocketException or RoomException) { }
            finally
            {
                stop.Cancel();
                try { await sender; } catch (Exception error) when (error is OperationCanceledException or WebSocketException) { }
                if (socket.State == WebSocketState.CloseReceived)
                {
                    using CancellationTokenSource timeout = new(TimeSpan.FromSeconds(2));
                    try { await socket.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, "closed", timeout.Token); }
                    catch (Exception error) when (error is OperationCanceledException or WebSocketException) { }
                }
                socket.Abort();
            }
        }
        finally { detach(stream); }
    }

    private static async Task Send(WebSocket socket, RoomStream stream, CancellationToken token)
    {
        await foreach (byte[] bytes in stream.Frames.Reader.ReadAllAsync(token))
        {
            await socket.SendAsync(bytes, WebSocketMessageType.Text, endOfMessage: true, token);
        }
    }

    private static async Task<byte[]?> Receive(WebSocket socket, CancellationToken token)
    {
        using MemoryStream bytes = new();
        byte[] buffer = new byte[4096];
        while (true)
        {
            WebSocketReceiveResult result = await socket.ReceiveAsync(buffer, token);
            if (result.MessageType == WebSocketMessageType.Close) { return null; }
            Wire.Need(result.MessageType == WebSocketMessageType.Text);
            Wire.Need(bytes.Length + result.Count <= Wire.MaximumBytes, "frameTooLarge", 413);
            bytes.Write(buffer, 0, result.Count);
            if (result.EndOfMessage) { return bytes.ToArray(); }
        }
    }
}
