import struct
import time


def linear16_to_ulaw(sample: int) -> int:
    """Encode one signed PCM16 sample using ITU-T G.711 mu-law."""
    bias = 0x84
    clip = 32635
    sign = 0x80 if sample < 0 else 0
    magnitude = min(-sample if sample < 0 else sample, clip) + bias
    exponent = max(0, min(7, magnitude.bit_length() - 8))
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


class G711Ulaw8kEncoder:
    """Stream PCM16LE 24 kHz mono into G.711 mu-law 8 kHz mono."""

    def __init__(self) -> None:
        self._pending_bytes = b""
        self._pending_samples: list[int] = []

    def encode(self, pcm: bytes) -> bytes:
        combined = self._pending_bytes + pcm
        even_length = len(combined) - len(combined) % 2
        self._pending_bytes = combined[even_length:]
        if even_length:
            self._pending_samples.extend(
                struct.unpack(f"<{even_length // 2}h", combined[:even_length])
            )

        complete = len(self._pending_samples) // 3 * 3
        if not complete:
            return b""
        encoded = bytearray(complete // 3)
        output_index = 0
        for index in range(0, complete, 3):
            first, middle, last = self._pending_samples[index : index + 3]
            encoded[output_index] = linear16_to_ulaw((first + middle + last) // 3)
            output_index += 1
        del self._pending_samples[:complete]
        return bytes(encoded)


class RealtimeAudioPacer:
    def __init__(self, bytes_per_second: int = 8_000, initial_buffer_bytes: int = 4_000) -> None:
        self.bytes_per_second = bytes_per_second
        self.initial_buffer_bytes = initial_buffer_bytes
        self.started_at: float | None = None
        self.sent_bytes = 0

    def delay_for(self, next_bytes: int, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        if self.started_at is None:
            self.started_at = current
        target_elapsed = max(
            0.0,
            (self.sent_bytes + next_bytes - self.initial_buffer_bytes) / self.bytes_per_second,
        )
        return max(0.0, target_elapsed - (current - self.started_at))

    def record_sent(self, count: int) -> None:
        self.sent_bytes += count
