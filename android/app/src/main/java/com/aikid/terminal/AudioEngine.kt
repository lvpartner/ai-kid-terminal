package com.aikid.terminal

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import androidx.core.content.ContextCompat
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread

class AudioEngine(private val context: Context) {
    data class PlaybackStats(val queuedPcmBytes: Int, val underrunCount: Int)

    private val recording = AtomicBoolean(false)
    private val playing = AtomicBoolean(true)
    private val initialBufferPending = AtomicBoolean(true)
    private val playbackQueue = ArrayBlockingQueue<ByteArray>(512)
    private val queuedBytes = AtomicInteger(0)
    private var recorder: AudioRecord? = null
    private val outputBufferSize = maxOf(
        8_000,
        AudioTrack.getMinBufferSize(
            8_000,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        ) * 2,
    )
    private val player = AudioTrack.Builder()
        .setAudioAttributes(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANCE_ACCESSIBILITY)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build(),
        )
        .setAudioFormat(
            AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(8_000)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                .build(),
        )
        .setTransferMode(AudioTrack.MODE_STREAM)
        .setBufferSizeInBytes(outputBufferSize)
        .build()
        .apply { play() }
    private val playbackThread = thread(name = "voice-playback") {
        while (playing.get()) {
            try {
                val data = playbackQueue.take()
                queuedBytes.addAndGet(-data.size)
                if (initialBufferPending.compareAndSet(true, false)) {
                    val deadline = System.nanoTime() + 120_000_000L
                    while (data.size + queuedBytes.get() < 12_000 &&
                        System.nanoTime() < deadline &&
                        playing.get()
                    ) {
                        Thread.sleep(5)
                    }
                }
                var offset = 0
                while (offset < data.size && playing.get()) {
                    val written = synchronized(player) {
                        player.write(data, offset, data.size - offset, AudioTrack.WRITE_BLOCKING)
                    }
                    if (written <= 0) break
                    offset += written
                }
            } catch (_: InterruptedException) {
                break
            }
        }
    }

    @SuppressLint("MissingPermission")
    fun start(onFrame: (ByteArray) -> Unit): Boolean {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) !=
            PackageManager.PERMISSION_GRANTED
        ) return false
        if (!recording.compareAndSet(false, true)) return true
        val bufferSize = maxOf(
            3_200,
            AudioRecord.getMinBufferSize(
                16_000,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
            ),
        )
        var candidate: AudioRecord? = null
        try {
            val record = AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                16_000,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufferSize,
            )
            candidate = record
            if (record.state != AudioRecord.STATE_INITIALIZED) {
                record.release()
                recording.set(false)
                return false
            }
            record.startRecording()
            if (record.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                record.release()
                recording.set(false)
                return false
            }
            recorder = record
            candidate = null
            thread(name = "voice-capture") {
                val buffer = ByteArray(3_200)
                while (recording.get()) {
                    val count = record.read(buffer, 0, buffer.size)
                    if (count > 0) onFrame(buffer.copyOf(count))
                }
            }
        } catch (_: RuntimeException) {
            candidate?.runCatching { release() }
            recorder?.runCatching { release() }
            recorder = null
            recording.set(false)
            return false
        }
        return true
    }

    fun stop() {
        if (!recording.compareAndSet(true, false)) return
        recorder?.runCatching { stop() }
        recorder?.release()
        recorder = null
    }

    fun playUlaw(data: ByteArray) {
        val pcm = G711Ulaw.decode(data)
        try {
            // Backpressure is preferable to audible gaps: never discard a valid PCM block.
            queuedBytes.addAndGet(pcm.size)
            playbackQueue.put(pcm)
        } catch (_: InterruptedException) {
            queuedBytes.addAndGet(-pcm.size)
            Thread.currentThread().interrupt()
        }
    }

    fun beginResponse() {
        initialBufferPending.set(true)
    }

    fun playbackStats() = PlaybackStats(
        queuedPcmBytes = queuedBytes.get(),
        underrunCount = player.underrunCount,
    )

    fun interruptPlayback() {
        playbackQueue.clear()
        queuedBytes.set(0)
        initialBufferPending.set(true)
        synchronized(player) {
            player.pause()
            player.flush()
            player.play()
        }
    }

    fun release() {
        stop()
        playing.set(false)
        playbackThread.interrupt()
        playbackThread.join(1_000)
        synchronized(player) {
            player.stop()
            player.release()
        }
    }
}
