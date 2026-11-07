# Running the room service

The room metadata service uses .NET SDK 10.0.402 and the ASP.NET Core shared framework. It is independent of the desktop UI and has no registry packages. Its [architecture and API](architecture/room-service.md) describe authorization, catalogs, replay, and limits.

## Build and check

Run from the repository root on Windows x64. C# service checks run only on Windows:

```powershell
git submodule update --init --checkout --depth=1 -- 3rdparty/fastjsonschema
python tools/server/check.py --provision --admission-runtime <owned-admission-helper>
```

With an existing SDK, omit `--provision` or pass `--dotnet <path-to-dotnet>`. This checks the exact version, restores SDK/local references with the empty registry configuration, checks formatting, builds, runs real HTTP/WebSocket and TLS Syncplay admission/lifecycle tests, and validates emitted v1 metadata and v2 account/room responses with the pinned contract validator. Supply the matching source-built helper package; required Windows CI uses the helper from its assembled application. `--provision` obtains a missing SDK from the official archive pinned by SHA-512 in [the manifest](../tools/server/toolchains.json).

The required native Windows job runs these service checks after assembling its verified application and helper. A successful Windows artifact requires a successful service stage. The room-metadata gate evaluates that complete job result, including failures and skips; it does not launch duplicate server builds or a C# toolchain on macOS/Linux. All three native client jobs and shared contract checks remain required.

Equivalent build commands are:

```powershell
dotnet restore services/room-metadata/RoomMetadata.slnx --configfile NuGet.Config
dotnet format services/room-metadata/RoomMetadata.slnx --no-restore --verify-no-changes
dotnet build services/room-metadata/RoomMetadata.slnx --no-restore --configuration Debug
```

## Local startup

For legacy metadata-only operation, generate a private creation key in Windows PowerShell. Authenticated deployment uses the [v2 authority configuration](authenticated-room-service.md), disables the legacy room routes, and requires no creation key.

```powershell
$env:SYNCWATCH_CREATE_KEY = python -c "import secrets; print(secrets.token_hex(32))"
dotnet run --project services/room-metadata/RoomMetadata --no-launch-profile
```

The default address is `http://127.0.0.1:8788`. `GET /health` reports liveness. The creation key must contain 32–256 characters; there is no built-in credential. Creation uses this key in an Authorization header. Member operations use their separate admission credential. The application does not print credentials, invitations, meeting links, or request bodies.

## Hosting and configuration

Use standard ASP.NET Core URL and Kestrel certificate configuration for HTTPS. HTTP is accepted only when both ends are loopback. A forwarding proxy does not enable external plaintext access: forwarded headers are not trusted. Browser-origin access and cookie/query-string authentication are disabled.

Configuration under `SyncWatch:Limits` adjusts validated bounds. Environment variable examples are `SyncWatch__Limits__MaximumRooms`, `SyncWatch__Limits__MaximumMembers`, and `SyncWatch__Limits__CatalogItems`. Durations such as `SyncWatch__Limits__RoomLifetime` use TimeSpan format. Invalid limits prevent startup. Defaults are listed in the [architecture](architecture/room-service.md#resource-bounds-and-storage).

Stopping/restarting discards rooms, credentials, catalogs, and history. This service does not start Syncplay, join Meet, or open movies. A successful health check does not establish those connections.
