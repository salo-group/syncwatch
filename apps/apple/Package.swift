// swift-tools-version: 6.1
import PackageDescription

let package = Package(
    name: "SyncWatchApple",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "SyncWatch", targets: ["SyncWatch"])],
    dependencies: [.package(path: "../../packages/apple-domain")],
    targets: [
        .executableTarget(
            name: "SyncWatch",
            dependencies: [.product(name: "SyncWatchDomain", package: "apple-domain")]
        )
    ]
)
