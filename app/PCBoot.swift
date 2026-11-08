import SwiftUI
import AppKit

@main
struct PCBootApp: App {
    var body: some Scene {
        WindowGroup("PC Boot") {
            ContentView()
                .frame(width: 440)
        }
        .windowResizability(.contentSize)
    }
}

enum PCState: String {
    case unknown, checking, off, linux, windows

    var label: String {
        switch self {
        case .unknown: return "Unknown"
        case .checking: return "Checking…"
        case .off: return "Off"
        case .linux: return "Linux"
        case .windows: return "Windows"
        }
    }
    var color: Color {
        switch self {
        case .off: return .red
        case .linux, .windows: return .green
        default: return .gray
        }
    }
}

final class Runner: ObservableObject {
    @Published var log = ""
    @Published var state: PCState = .checking
    @Published var busy = false
    @Published var target: String? = nil
    @Published var openMoonlight = true
    private var proc: Process?

    private var script: String {
        Bundle.main.path(forResource: "turnon", ofType: "py") ?? ""
    }
    private var python: String {
        ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
            .first { FileManager.default.isExecutableFile(atPath: $0) } ?? "/usr/bin/python3"
    }

    private func makeProcess(_ arg: String) -> Process {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-u", "-B", script, arg]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["PYTHONUNBUFFERED"] = "1"
        if !openMoonlight { env["NO_MOONLIGHT"] = "1" }
        p.environment = env
        p.standardInput = FileHandle.nullDevice
        return p
    }

    func refresh() {
        guard !busy else { return }
        DispatchQueue.main.async { self.state = .checking }
        DispatchQueue.global().async {
            let p = self.makeProcess("status")
            let pipe = Pipe()
            p.standardOutput = pipe
            p.standardError = Pipe()
            try? p.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit()
            let out = (String(data: data, encoding: .utf8) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            DispatchQueue.main.async {
                if !self.busy { self.state = PCState(rawValue: out) ?? .unknown }
            }
        }
    }

    func boot(_ os: String) {
        guard !busy else { return }
        busy = true
        target = os
        log = ""
        let p = makeProcess(os)
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { h in
            let d = h.availableData
            guard !d.isEmpty, let s = String(data: d, encoding: .utf8) else { return }
            DispatchQueue.main.async { self.log += s.replacingOccurrences(of: "\r", with: "") }
        }
        p.terminationHandler = { proc in
            pipe.fileHandleForReading.readabilityHandler = nil
            DispatchQueue.main.async {
                self.log += proc.terminationStatus == 0 ? "\n✓ Finished\n" : "\n✗ Stopped (exit \(proc.terminationStatus))\n"
                self.busy = false
                self.target = nil
                self.proc = nil
                self.refresh()
            }
        }
        do { try p.run(); proc = p } catch {
            log = "Couldn't start: \(error.localizedDescription)"
            busy = false
            target = nil
        }
    }

    func cancel() { proc?.terminate() }
}

struct OSButton: View {
    let title: String
    let symbol: String
    let tint: Color
    let isRunning: Bool
    let disabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 10) {
                if isRunning {
                    ProgressView().controlSize(.large).frame(height: 34)
                } else {
                    Image(systemName: symbol).font(.system(size: 30, weight: .medium)).frame(height: 34)
                }
                Text(title).font(.headline)
            }
            .frame(maxWidth: .infinity, minHeight: 110)
            .background(RoundedRectangle(cornerRadius: 16).fill(tint.opacity(0.14)))
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(tint.opacity(0.45), lineWidth: 1))
            .foregroundStyle(tint)
            .contentShape(RoundedRectangle(cornerRadius: 16))
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .opacity(disabled && !isRunning ? 0.45 : 1)
    }
}

struct ContentView: View {
    @StateObject private var r = Runner()
    @State private var showLog = true

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("PC Boot").font(.title2.weight(.semibold))
                    HStack(spacing: 6) {
                        Circle().fill(r.state.color).frame(width: 8, height: 8)
                        Text(r.busy ? "Working…" : r.state.label)
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Button { r.refresh() } label: { Image(systemName: "arrow.clockwise") }
                    .buttonStyle(.borderless).disabled(r.busy).help("Check PC status")
            }

            HStack(spacing: 14) {
                OSButton(title: "Linux", symbol: "terminal", tint: .orange,
                         isRunning: r.target == "linux", disabled: r.busy) { r.boot("linux") }
                OSButton(title: "Windows", symbol: "macwindow", tint: .blue,
                         isRunning: r.target == "windows", disabled: r.busy) { r.boot("windows") }
            }

            Toggle("Open Moonlight when ready", isOn: $r.openMoonlight)
                .toggleStyle(.checkbox).disabled(r.busy)

            DisclosureGroup("Log", isExpanded: $showLog) {
                ScrollViewReader { proxy in
                    ScrollView {
                        Text(r.log.isEmpty ? "Nothing yet." : r.log)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(r.log.isEmpty ? .secondary : .primary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                            .padding(8)
                        Color.clear.frame(height: 1).id("end")
                    }
                    .frame(height: 150)
                    .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.06)))
                    .onChange(of: r.log) { proxy.scrollTo("end") }
                }
            }

            if r.busy {
                Button("Cancel", role: .cancel) { r.cancel() }
            }
        }
        .padding(20)
        .onAppear { r.refresh() }
    }
}
