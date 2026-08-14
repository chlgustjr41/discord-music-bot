namespace Loupedeck.JackyControlPlugin;

using System;
using System.Collections.Generic;
using System.IO;
using NAudio.Wave;

/// <summary>16 kHz mono 16-bit WAV captured entirely in memory. The recording
/// never touches disk and is discarded after the POST.</summary>
public sealed class MicRecorder : IDisposable
{
    public const Int32 MaxSeconds = 15;
    private WaveInEvent _waveIn;
    private MemoryStream _buffer;
    private WaveFileWriter _writer;

    public static IReadOnlyList<(Int32 Number, String Name)> Devices()
    {
        var list = new List<(Int32, String)>();
        for (var i = 0; i < WaveInEvent.DeviceCount; i++)
        {
            list.Add((i, WaveInEvent.GetCapabilities(i).ProductName));
        }
        return list;
    }

    public Boolean Recording => this._waveIn != null;
    public event EventHandler MaxDurationReached;

    public void Start(Int32 deviceNumber)
    {
        if (this.Recording)
        {
            return;
        }
        this._buffer = new MemoryStream();
        this._waveIn = new WaveInEvent { DeviceNumber = deviceNumber, WaveFormat = new WaveFormat(16000, 16, 1) };
        this._writer = new WaveFileWriter(new NAudio.Utils.IgnoreDisposeStream(this._buffer), this._waveIn.WaveFormat);
        this._waveIn.DataAvailable += (_, e) =>
        {
            this._writer.Write(e.Buffer, 0, e.BytesRecorded);
            if (this._writer.TotalTime.TotalSeconds >= MaxSeconds)
            {
                this.MaxDurationReached?.Invoke(this, EventArgs.Empty);
            }
        };
        try
        {
            this._waveIn.StartRecording();
        }
        catch
        {
            // A stale device number (mic unplugged since the editor filled the
            // listbox) throws MmException here. Recording must read false
            // afterwards, or every later press silently no-ops against a
            // recorder that never started.
            this._writer.Dispose();
            this._writer = null;
            this._waveIn.Dispose();
            this._waveIn = null;
            this._buffer.Dispose();
            this._buffer = null;
            throw;
        }
    }

    /// <summary>Stops and returns the complete WAV, or null if nothing was captured.</summary>
    public Byte[] Stop()
    {
        if (!this.Recording)
        {
            return null;
        }
        this._waveIn.StopRecording();
        this._writer.Dispose();          // finalizes the WAV header into _buffer
        this._waveIn.Dispose();
        this._waveIn = null;
        var wav = this._buffer.ToArray();
        this._buffer.Dispose();
        // A header-only file means zero audio frames — treat as no capture,
        // mirroring the Stream Deck plugin's zero-byte lesson.
        return wav.Length > 44 ? wav : null;
    }

    public void Dispose() => this.Stop();
}
