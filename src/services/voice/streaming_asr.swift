// streaming_asr.swift — Real-time macOS speech recognition via SFSpeechRecognizer
// Reads raw 16-bit PCM audio (16kHz mono) from stdin, outputs partial/final
// transcription results as JSON lines to stdout.
//
// Build:  swiftc -O -o streaming_asr streaming_asr.swift -framework Speech -framework AVFoundation
// Usage:  echo audio | ./streaming_asr [--lang en-US]
//
// Output protocol (one JSON per line):
//   {"type":"partial","text":"hello wor"}
//   {"type":"final","text":"hello world"}
//   {"type":"error","text":"description"}
//   {"type":"ready"}
//   {"type":"end"}

import Foundation
import Speech
import AVFoundation

// MARK: - Helpers

func writeJSON(_ dict: [String: String]) {
    guard let data = try? JSONSerialization.data(withJSONObject: dict),
          let str = String(data: data, encoding: .utf8) else { return }
    FileHandle.standardOutput.write(Data((str + "\n").utf8))
}

func exitWithError(_ msg: String) -> Never {
    writeJSON(["type": "error", "text": msg])
    exit(1)
}

// MARK: - Parse args

var localeId = "en-US"
let args = CommandLine.arguments
if let idx = args.firstIndex(of: "--lang"), idx + 1 < args.count {
    localeId = args[idx + 1]
}

// MARK: - Authorization

let authSema = DispatchSemaphore(value: 0)
var authGranted = false

SFSpeechRecognizer.requestAuthorization { status in
    authGranted = (status == .authorized)
    authSema.signal()
}
authSema.wait()

guard authGranted else {
    exitWithError("Speech recognition not authorized. Grant permission in System Settings > Privacy > Speech Recognition.")
}

// MARK: - Setup recognizer

func makeRecognizer() -> SFSpeechRecognizer {
    let locale = Locale(identifier: localeId)
    if let r = SFSpeechRecognizer(locale: locale), r.isAvailable {
        return r
    }
    // Try English fallback
    if localeId != "en-US" {
        writeJSON(["type": "error", "text": "Locale \(localeId) unavailable, trying en-US"])
    }
    if let r = SFSpeechRecognizer(locale: Locale(identifier: "en-US")), r.isAvailable {
        return r
    }
    exitWithError("SFSpeechRecognizer not available")
}

let finalRecognizer = makeRecognizer()

// MARK: - Audio format (16kHz 16-bit mono PCM — matches Python listener)

let audioFormat = AVAudioFormat(
    commonFormat: .pcmFormatInt16,
    sampleRate: 16000,
    channels: 1,
    interleaved: true
)!

// MARK: - Recognition request

let request = SFSpeechAudioBufferRecognitionRequest()
request.shouldReportPartialResults = true
// Use on-device only if --offline flag is passed; otherwise let Apple pick the best available model
if args.contains("--offline") {
    request.requiresOnDeviceRecognition = true
}

// For macOS 13+ we can add punctuation
if #available(macOS 13, *) {
    request.addsPunctuation = true
}

writeJSON(["type": "ready"])

// MARK: - Start recognition task

var lastPartial = ""
let doneSema = DispatchSemaphore(value: 0)

let task = finalRecognizer.recognitionTask(with: request) { result, error in
    if let result = result {
        let text = result.bestTranscription.formattedString
        if result.isFinal {
            writeJSON(["type": "final", "text": text])
            doneSema.signal()
        } else if text != lastPartial {
            lastPartial = text
            writeJSON(["type": "partial", "text": text])
        }
    }
    if let error = error {
        let nsErr = error as NSError
        // Code 1101 = "no speech detected" — not a real error
        if nsErr.code == 1101 {
            writeJSON(["type": "final", "text": ""])
        } else {
            writeJSON(["type": "error", "text": error.localizedDescription])
        }
        doneSema.signal()
    }
}

// MARK: - Read stdin and feed audio buffers

let stdinHandle = FileHandle.standardInput
let bytesPerSample = 2  // Int16
let samplesPerChunk = 1600  // 100ms at 16kHz
let chunkByteSize = samplesPerChunk * bytesPerSample

// Read audio from stdin in chunks and append to recognition request
DispatchQueue.global(qos: .userInteractive).async {
    while true {
        let data = stdinHandle.readData(ofLength: chunkByteSize)
        if data.isEmpty {
            // stdin closed — end the audio stream
            request.endAudio()
            break
        }

        // Convert raw bytes to AVAudioPCMBuffer
        let sampleCount = data.count / bytesPerSample
        guard let buffer = AVAudioPCMBuffer(pcmFormat: audioFormat, frameCapacity: AVAudioFrameCount(sampleCount)) else {
            continue
        }
        buffer.frameLength = AVAudioFrameCount(sampleCount)

        data.withUnsafeBytes { rawBuf in
            if let src = rawBuf.baseAddress {
                memcpy(buffer.int16ChannelData![0], src, data.count)
            }
        }

        request.append(buffer)
    }
}

// Wait for recognition to finish (stdin EOF → endAudio → final result)
let waitResult = doneSema.wait(timeout: .now() + 30.0)
if waitResult == .timedOut {
    task.cancel()
    writeJSON(["type": "error", "text": "Recognition timed out"])
}

writeJSON(["type": "end"])
