//! Bit I/O primitives used by the SMPTE RDD 36 entropy coder.
//!
//! ProRes coefficients are coded with Golomb-Rice / exponential-Golomb
//! combination codes; the actual codeword decoders live in
//! [`crate::entropy`]. This module only provides the underlying MSB-first
//! bit reader / writer.

use oxideav_core::{Error, Result};

/// MSB-first bit reader with byte-aligned backing buffer.
pub struct BitReader<'a> {
    buf: &'a [u8],
    bit_pos: usize, // absolute bit offset from start of `buf`
}

impl<'a> BitReader<'a> {
    pub fn new(buf: &'a [u8]) -> Self {
        Self { buf, bit_pos: 0 }
    }

    /// Read a single bit (0 or 1).
    pub fn read_bit(&mut self) -> Result<u32> {
        let byte_idx = self.bit_pos / 8;
        if byte_idx >= self.buf.len() {
            return Err(Error::invalid("prores bitstream: EOF"));
        }
        let bit_idx = 7 - (self.bit_pos % 8);
        self.bit_pos += 1;
        Ok(((self.buf[byte_idx] >> bit_idx) & 1) as u32)
    }

    /// Read the next `n` bits (MSB first). `n` must be in `1..=32`.
    pub fn read_bits(&mut self, n: u32) -> Result<u32> {
        debug_assert!((1..=32).contains(&n));
        let mut v = 0u32;
        for _ in 0..n {
            v = (v << 1) | self.read_bit()?;
        }
        Ok(v)
    }

    /// Approximation of the spec's `endOfData(dataSize)` predicate
    /// (RDD 36 §5.2): true if the number of remaining bits is 31 or
    /// fewer and any remaining bits are zero. This is used to terminate
    /// the AC-coefficient run-level loop.
    ///
    /// A correct implementation requires knowing the slice payload's
    /// exact byte length (the buffer we were constructed from is sized
    /// to that — `data` should be the per-component slice data only).
    pub fn end_of_data(&self) -> bool {
        let total_bits = self.buf.len() * 8;
        if self.bit_pos >= total_bits {
            return true;
        }
        let remaining = total_bits - self.bit_pos;
        if remaining > 31 {
            return false;
        }
        // Any remaining bit non-zero → not at EOD yet.
        let mut pos = self.bit_pos;
        while pos < total_bits {
            let byte_idx = pos / 8;
            let bit_idx = 7 - (pos % 8);
            if ((self.buf[byte_idx] >> bit_idx) & 1) != 0 {
                return false;
            }
            pos += 1;
        }
        true
    }

    /// How many whole bytes we've consumed, rounding up any partial byte.
    pub fn byte_pos(&self) -> usize {
        self.bit_pos.div_ceil(8)
    }

    /// Current bit position (absolute, from start of buffer).
    pub fn bit_pos(&self) -> usize {
        self.bit_pos
    }
}

/// MSB-first bit writer.
pub struct BitWriter {
    buf: Vec<u8>,
    /// Bit offset into the *last* byte of `buf` where the next write lands.
    /// `0..=7`. When it hits 8 we push a fresh byte.
    cur_bits_used: u32,
}

impl Default for BitWriter {
    fn default() -> Self {
        Self::new()
    }
}

impl BitWriter {
    pub fn new() -> Self {
        Self {
            buf: Vec::new(),
            cur_bits_used: 8,
        }
    }

    pub fn write_bit(&mut self, b: u32) {
        if self.cur_bits_used == 8 {
            self.buf.push(0);
            self.cur_bits_used = 0;
        }
        let last = self.buf.last_mut().unwrap();
        let shift = 7 - self.cur_bits_used;
        *last |= ((b & 1) as u8) << shift;
        self.cur_bits_used += 1;
    }

    pub fn write_bits(&mut self, v: u32, n: u32) {
        debug_assert!((1..=32).contains(&n));
        for i in (0..n).rev() {
            self.write_bit((v >> i) & 1);
        }
    }

    /// Pad to the next byte boundary with zero bits.
    pub fn align_byte(&mut self) {
        while self.cur_bits_used != 8 {
            self.write_bit(0);
        }
    }

    pub fn finish(mut self) -> Vec<u8> {
        self.align_byte();
        self.buf
    }

    pub fn byte_len(&self) -> usize {
        self.buf.len()
    }

    /// Number of bits written so far (including any partial trailing byte).
    pub fn bit_len(&self) -> usize {
        if self.cur_bits_used == 8 {
            self.buf.len() * 8
        } else {
            (self.buf.len() - 1) * 8 + self.cur_bits_used as usize
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bits_roundtrip() {
        let mut w = BitWriter::new();
        w.write_bits(0b1010_1100, 8);
        w.write_bits(0x5A_5A, 16);
        w.write_bit(1);
        w.write_bit(0);
        let data = w.finish();
        let mut r = BitReader::new(&data);
        assert_eq!(r.read_bits(8).unwrap(), 0b1010_1100);
        assert_eq!(r.read_bits(16).unwrap(), 0x5A_5A);
        assert_eq!(r.read_bit().unwrap(), 1);
        assert_eq!(r.read_bit().unwrap(), 0);
    }

    #[test]
    fn end_of_data_with_zero_padding() {
        // Buffer of one byte 0b10000000 (just the bit '1' followed by 7 zero pads).
        let buf = [0b1000_0000u8];
        let mut r = BitReader::new(&buf);
        assert!(!r.end_of_data()); // 8 bits left, not at EOD
        let _ = r.read_bit().unwrap();
        // Now 7 zero bits remain; EOD should be true.
        assert!(r.end_of_data());
    }

    #[test]
    fn end_of_data_with_nonzero_padding() {
        let buf = [0b1000_0001u8];
        let mut r = BitReader::new(&buf);
        let _ = r.read_bit().unwrap(); // consume the leading 1
                                       // Trailing bit is non-zero → not yet at EOD.
        assert!(!r.end_of_data());
    }
}
