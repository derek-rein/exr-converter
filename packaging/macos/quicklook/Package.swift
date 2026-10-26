// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "EXRQuickLook",
    platforms: [.macOS(.v12)],
    products: [
        .library(name: "EXRQLCore", targets: ["EXRQLCore"]),
    ],
    targets: [
        .target(name: "EXRQLCore", path: "Sources/EXRQLCore"),
        .testTarget(
            name: "EXRQLCoreTests",
            dependencies: ["EXRQLCore"],
            path: "Tests/EXRQLCoreTests"
        ),
    ]
)
