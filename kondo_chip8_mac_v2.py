#!/usr/bin/env python3
# Kondo's CHIP-8 Emu — macOS-safe build
# Python 3.14+ / pygame-ce
# [C] AC Kondo 1999-2026
# Original CHIP-8: Joseph Weisbecker, 1977

import os
import sys
import random
from pathlib import Path

# Keep SDL/Pygame as the only GUI stack. Mixing Tk/Cocoa and SDL in one process
# can trigger uncaught macOS NSException crashes on newer macOS releases.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame

TITLE = "Kondo's CHIP-8 Emu"
WIDTH, HEIGHT = 64, 32
SCALE = 14
FPS = 60
CPU_HZ = 720
MEM_SIZE = 4096
PROGRAM_START = 0x200

FONTSET = [
    0xF0,0x90,0x90,0x90,0xF0, 0x20,0x60,0x20,0x20,0x70,
    0xF0,0x10,0xF0,0x80,0xF0, 0xF0,0x10,0xF0,0x10,0xF0,
    0x90,0x90,0xF0,0x10,0x10, 0xF0,0x80,0xF0,0x10,0xF0,
    0xF0,0x80,0xF0,0x90,0xF0, 0xF0,0x10,0x20,0x40,0x40,
    0xF0,0x90,0xF0,0x90,0xF0, 0xF0,0x90,0xF0,0x10,0xF0,
    0xF0,0x90,0xF0,0x90,0x90, 0xE0,0x90,0xE0,0x90,0xE0,
    0xF0,0x80,0x80,0x80,0xF0, 0xE0,0x90,0x90,0x90,0xE0,
    0xF0,0x80,0xF0,0x80,0xF0, 0xF0,0x80,0xF0,0x80,0x80,
]

KEYMAP = {
    pygame.K_1:0x1, pygame.K_2:0x2, pygame.K_3:0x3, pygame.K_4:0xC,
    pygame.K_q:0x4, pygame.K_w:0x5, pygame.K_e:0x6, pygame.K_r:0xD,
    pygame.K_a:0x7, pygame.K_s:0x8, pygame.K_d:0x9, pygame.K_f:0xE,
    pygame.K_z:0xA, pygame.K_x:0x0, pygame.K_c:0xB, pygame.K_v:0xF,
}

class Chip8:
    def __init__(self):
        self.memory = bytearray(MEM_SIZE)
        self.v = [0] * 16
        self.i = 0
        self.pc = PROGRAM_START
        self.stack = []
        self.delay_timer = 0
        self.sound_timer = 0
        self.display = [0] * (WIDTH * HEIGHT)
        self.keys = [False] * 16
        self.waiting_for_key = None
        self.draw_flag = True
        self.rom_name = "NO ROM"
        self.status_message = "Pass or drag a .ch8 ROM"
        self.status_timer = 0
        self._last_drop = None
        self.memory[0x50:0x50+len(FONTSET)] = bytes(FONTSET)

    def reset(self):
        self.__init__()

    def load_rom(self, filename):
        p = Path(filename)

        if not p.is_file():
            raise ValueError("Dropped path is not a file.")

        # Avoid macOS sending the same drag payload repeatedly.
        key = (str(p.resolve()), p.stat().st_size)
        if key == self._last_drop:
            return False
        self._last_drop = key

        data = p.read_bytes()
        max_rom = MEM_SIZE - PROGRAM_START  # 4096 - 0x200 = 3584 bytes

        if len(data) > max_rom:
            raise ValueError(
                f"{p.name} is {len(data)} bytes; classic CHIP-8 max is {max_rom} bytes."
            )

        self.reset()
        self._last_drop = key
        self.memory[PROGRAM_START:PROGRAM_START+len(data)] = data
        self.rom_name = p.name
        self.status_message = f"Loaded {p.name}"
        self.status_timer = 180
        self.draw_flag = True
        print(f"Loaded: {p} ({len(data)} bytes)")
        return True

    def cycle(self):
        if self.waiting_for_key is not None:
            return
        if self.pc >= MEM_SIZE - 1:
            raise RuntimeError(f"PC out of range: 0x{self.pc:03X}")

        op = (self.memory[self.pc] << 8) | self.memory[self.pc + 1]
        self.pc = (self.pc + 2) & 0xFFF

        nnn, nn, n = op & 0xFFF, op & 0xFF, op & 0xF
        x, y = (op >> 8) & 0xF, (op >> 4) & 0xF
        h = op & 0xF000

        if op == 0x00E0:
            self.display[:] = [0] * (WIDTH * HEIGHT)
            self.draw_flag = True
        elif op == 0x00EE:
            if not self.stack:
                raise RuntimeError("Stack underflow on 00EE")
            self.pc = self.stack.pop()
        elif h == 0x0000:
            pass
        elif h == 0x1000: self.pc = nnn
        elif h == 0x2000:
            if len(self.stack) >= 16:
                raise RuntimeError("CHIP-8 stack overflow")
            self.stack.append(self.pc); self.pc = nnn
        elif h == 0x3000:
            if self.v[x] == nn: self.pc += 2
        elif h == 0x4000:
            if self.v[x] != nn: self.pc += 2
        elif h == 0x5000 and n == 0:
            if self.v[x] == self.v[y]: self.pc += 2
        elif h == 0x6000: self.v[x] = nn
        elif h == 0x7000: self.v[x] = (self.v[x] + nn) & 0xFF
        elif h == 0x8000: self._alu(x, y, n)
        elif h == 0x9000 and n == 0:
            if self.v[x] != self.v[y]: self.pc += 2
        elif h == 0xA000: self.i = nnn
        elif h == 0xB000: self.pc = (nnn + self.v[0]) & 0xFFF
        elif h == 0xC000: self.v[x] = random.randrange(256) & nn
        elif h == 0xD000: self._draw(self.v[x], self.v[y], n)
        elif h == 0xE000:
            k = self.v[x] & 0xF
            if nn == 0x9E and self.keys[k]: self.pc += 2
            elif nn == 0xA1 and not self.keys[k]: self.pc += 2
        elif h == 0xF000: self._fx(x, nn)
        else:
            print(f"Unknown opcode {op:04X} at {self.pc-2:03X}")

    def _alu(self, x, y, n):
        if n == 0: self.v[x] = self.v[y]
        elif n == 1: self.v[x] |= self.v[y]
        elif n == 2: self.v[x] &= self.v[y]
        elif n == 3: self.v[x] ^= self.v[y]
        elif n == 4:
            s = self.v[x] + self.v[y]; self.v[0xF] = int(s > 0xFF); self.v[x] = s & 0xFF
        elif n == 5:
            a,b=self.v[x],self.v[y]; self.v[0xF]=int(a>=b); self.v[x]=(a-b)&0xFF
        elif n == 6:
            self.v[0xF]=self.v[x]&1; self.v[x] >>= 1
        elif n == 7:
            a,b=self.v[x],self.v[y]; self.v[0xF]=int(b>=a); self.v[x]=(b-a)&0xFF
        elif n == 0xE:
            self.v[0xF]=(self.v[x]>>7)&1; self.v[x]=(self.v[x]<<1)&0xFF

    def _fx(self, x, nn):
        if nn == 0x07: self.v[x] = self.delay_timer
        elif nn == 0x0A: self.waiting_for_key = x
        elif nn == 0x15: self.delay_timer = self.v[x]
        elif nn == 0x18: self.sound_timer = self.v[x]
        elif nn == 0x1E: self.i = (self.i + self.v[x]) & 0xFFF
        elif nn == 0x29: self.i = 0x50 + (self.v[x] & 0xF) * 5
        elif nn == 0x33:
            v=self.v[x]
            self.memory[self.i:self.i+3]=bytes((v//100,(v//10)%10,v%10))
        elif nn == 0x55:
            for r in range(x+1): self.memory[(self.i+r)&0xFFF]=self.v[r]
        elif nn == 0x65:
            for r in range(x+1): self.v[r]=self.memory[(self.i+r)&0xFFF]

    def _draw(self, x, y, n):
        self.v[0xF] = 0
        for row in range(n):
            sprite = self.memory[(self.i + row) & 0xFFF]
            for bit in range(8):
                if sprite & (0x80 >> bit):
                    px, py = (x + bit) % WIDTH, (y + row) % HEIGHT
                    idx = py * WIDTH + px
                    if self.display[idx]: self.v[0xF] = 1
                    self.display[idx] ^= 1
        self.draw_flag = True

    def key_down(self, key):
        c = KEYMAP.get(key)
        if c is None: return
        self.keys[c] = True
        if self.waiting_for_key is not None:
            self.v[self.waiting_for_key] = c
            self.waiting_for_key = None

    def key_up(self, key):
        c = KEYMAP.get(key)
        if c is not None: self.keys[c] = False

    def timers_60hz(self):
        if self.delay_timer: self.delay_timer -= 1
        if self.sound_timer: self.sound_timer -= 1


def main():
    # IMPORTANT: no tkinter on macOS. SDL/Cocoa owns the GUI lifecycle.
    pygame.display.init()
    pygame.font.init()

    try:
        screen = pygame.display.set_mode((WIDTH*SCALE, HEIGHT*SCALE))
    except pygame.error as exc:
        print("SDL display creation failed:", exc)
        print("SDL:", pygame.get_sdl_version())
        return 1

    pygame.display.set_caption(TITLE)
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 24)
    chip = Chip8()

    if len(sys.argv) > 1:
        try:
            chip.load_rom(sys.argv[1])
        except Exception as exc:
            print("ROM load failed:", exc)

    print("Kondo's CHIP-8 Emu")
    print("[C] AC Kondo 1999-2026")
    print("Original CHIP-8: Joseph Weisbecker, 1977")
    print("Run: python3 kondo_chip8_mac.py game.ch8")
    print("Or drag a .ch8 ROM onto the emulator window.")

    running = True
    cycle_accum = 0.0
    timer_accum = 0.0

    while running:
        dt = min(clock.tick(FPS) / 1000.0, 0.1)

        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    running = False
                else:
                    chip.key_down(e.key)
            elif e.type == pygame.KEYUP:
                chip.key_up(e.key)
            elif hasattr(pygame, "DROPFILE") and e.type == pygame.DROPFILE:
                try:
                    loaded = chip.load_rom(e.file)
                    if loaded:
                        pygame.display.set_caption(f"{TITLE} — {Path(e.file).name}")
                except Exception as exc:
                    # Keep running; report the error once in the window and console.
                    msg = str(exc)
                    chip.status_message = msg
                    chip.status_timer = 240
                    print("Dropped ROM rejected:", msg)

        if chip.rom_name != "NO ROM":
            cycle_accum += dt * CPU_HZ
            cycles = min(int(cycle_accum), 50)
            cycle_accum -= cycles
            try:
                for _ in range(cycles):
                    chip.cycle()
            except Exception as exc:
                print("CPU halted:", exc)
                chip.rom_name = "NO ROM"

            timer_accum += dt * 60.0
            while timer_accum >= 1.0:
                chip.timers_60hz()
                timer_accum -= 1.0

        screen.fill((12,18,30))
        for idx, on in enumerate(chip.display):
            if on:
                x = (idx % WIDTH) * SCALE
                y = (idx // WIDTH) * SCALE
                pygame.draw.rect(screen, (102,204,255), (x,y,SCALE,SCALE))

        if chip.rom_name == "NO ROM":
            msg = font.render("Pass or drag a .ch8 ROM", True, (220,220,220))
            screen.blit(msg, (20, 20))

        if chip.status_timer > 0 and chip.status_message:
            # Keep status readable even if the message is long.
            shown = chip.status_message
            if len(shown) > 72:
                shown = shown[:69] + "..."
            status = font.render(shown, True, (255, 210, 120))
            screen.blit(status, (20, HEIGHT * SCALE - 30))
            chip.status_timer -= 1

        pygame.display.flip()

    pygame.quit()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
