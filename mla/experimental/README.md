# experimental/ — frozen, purely theoretical

> **This directory is experimental and is not being developed further.** The content here remains as a reference/theoretical possibility, not as a target path for the project.

## `nic_mla_hal_nor.py` — raw SPI-NOR HAL (simulator)

NOR flash simulator of the W25Q class in RAM (`MlaNorSimHAL`) — models the limitations of raw NOR: Page Program in 256 B chunks, writes as AND (only 1→0), erase in sectors to 0xFF. Serves to test that the kernel behaves correctly even on media with erase-before-write semantics.

### Why it's frozen (decision)

Direct raw SPI-NOR/NAND was **abandoned** in favor of **SD/flash cards**:

- **Risk of chip lockdown.** Some NOR/NAND controllers have security / lockdown mechanisms; partial-page or partial-block writes (when you don't write the entire block) can put them into an error state after a few dozen blocks.
- **Vendor-specific.** Doing it properly would mean writing it for specific series (Winbond W25Q, etc.) — low universality, high maintenance.
- **SD has its own controller.** The card **handles wear-leveling, ECC and block remapping** on its own. For a station at ~15 min intervals, this is more reliable and simpler — even on Arduino we use the card.

### Consequences

- **No real SPI-NOR HAL is being written** (even in C). C libraries target SD (SdFat).
- The simulator and its tests remain functional (the kernel is independent of the medium via HAL), but this serves as a **proof of universality**, not a supported scenario.
- If you ever revive it, keep in mind that the commit protocol (LOCK first, flags outside CRC) is designed for NOR on purpose — but you'll need to verify lockdown risks for specific chips in their datasheets.

★ Viva La Resistánce ★
