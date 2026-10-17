// swift-tools-version: 6.1
import PackageDescription

let package = Package(
    name: "SyncWatchDomain",
    platforms: [.macOS(.v13)],
    products: [.library(name: "SyncWatchDomain", targets: ["SyncWatchDomain"])],
    targets: [
        .target(name: "SyncWatchDomain"),
        .testTarget(name: "SyncWatchDomainTests", dependencies: ["SyncWatchDomain"]),
    ]
)
