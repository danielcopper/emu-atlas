# Changelog

## [0.2.0](https://github.com/danielcopper/emu-atlas/compare/v0.1.0...v0.2.0) (2026-08-08)


### ⚠ BREAKING CHANGES

* **api:** the save-family names are renamed, old -> new: save_location -> savefile_location and state_location -> savestate_location (Installation protocol, EmulatorEntry, EveryInstallation); entry_save_location -> entry_savefile_location and entry_state_location -> entry_savestate_location (the entry routes); SavePlacement -> SavefilePlacement; placement_contract -> savefile_placement_contract; build_save_placement -> build_savefile_placement; vector input keys query -> savefile_query, state_query -> savestate_query, entry_query -> entry_savefile_query, entry_state_query -> entry_savestate_query; aggregate question names save_location -> savefile_location, state_location -> savestate_location.
* **placement:** RetroArchCfg now names the family it resolved, and its family-specific fields lost their savefile prefix — savefile_directory is directory, savefiles_in_content_dir is in_content_dir; LayoutDefaults follows. resolve_save_layout is resolve_layout and takes a required keys argument. The conformance vector contract is unchanged and additive.
* **catalogue:** RetroDeck.roms_dir() returns str | None and resolves ES-DE's ROMDirectory instead of retrodeck.json's roms_path; the per-game-override anchor follows the same setting; a wrong-typed roms_path no longer yields marker-invalid health findings; the per-game-selection vector's fixture was corrected to a machine that can exist.
* **evidence:** answers from a verified arrangement whose machine states a version different from the evidence pin now carry arrangement-version-drifted (one existing vector's expected block gains it); on a machine whose marker states an empty version, the per-card unverified-version caveat's data changes from verification "drifted" with an empty arrangement_live to verification "runtime-version-unknown", because empty is the arrangement's own spelling for unset.
* **api:** 65 names are no longer importable from the atlas top level and live in their modules (atlas.machine, atlas.retroarch_cfg, atlas.esde, atlas.core_info, atlas.firmware, atlas.oddities, atlas.evidence, atlas.placement); 16 new vocabulary names join the top level (net 184 → 138); RETRODECK_DEFAULTS, EMUDECK_DEFAULTS and interpret_cfg are removed; the arrangement classes and kind strings rename to BareRetroArchFlatpak/bare_retroarch_flatpak and BareRetroArchNative/bare_retroarch_native, including in vector installation selectors and evidence data.
* **api:** Python attribute renames (FileSet.provenance, Granularity.option_provenance, EmulatorSpec/EmulatorEntry.provenance; eight CAVEAT_* constant names — code strings unchanged); serialized catalogue entries gain "system" and their caveats serialize as {code, data} objects instead of bare code strings.
* **firmware:** standalone emulators answer declaration "unsupported" with caveat standalone-unsupported on the firmware route (previously "absent" with standalone-emulator); an absent system_directory key resolves to the platform default and answers fully instead of an empty answer with system-directory-unset; the retired codes standalone-emulator and system-directory-unset are refused by the vector validator; a cleared key answers system-directory-cleared.
* **installations:** firmware, catalogue, systems and identification answers from a broken installation now carry the health finding caveats in front of their own; previously only save placements did.
* **contract:** installations[].health serializes as a list of {code, data} finding objects instead of bare code strings; placement answers carry health finding codes directly and the health envelope caveat (code "health" with data["issue"]) no longer exists; CAVEAT_HEALTH is removed from the public API.
* **evidence:** every caveat-bearing answer from an arrangement without a live-verification record (emudeck, standalone_retroarch_flatpak, native_retroarch) now carries the arrangement-unverified caveat; existing vector expected blocks for those arrangements changed accordingly.
* **installations:** emulators_for and systems return CatalogueAnswer and SystemsAnswer instead of bare tuples, on every Installation handle. When the bundled es_systems.xml cannot be read, the answer is the refusal alone — entries a readable custom_systems overlay declared are no longer reported, because only the bundled layer decides whether the catalogue was read.
* **machine:** Machine.glob returns GlobResult instead of a bare list; ports must implement the explicit outcome and the unlistable fixture state; read_core_declarations returns CoreEnumeration; the machines vector schema is 3.
* **placement:** a card rooted in the system directory no longer emits a system_directory hole; consumers branching on it must handle the resolved answer, and such a placement may now report the content directory as its root kind.
* the per-game VMU mode's expectation changes from an unverified file set to a stated one, and fixture content is renamed in five vectors.
* four vector expectations change. Consumers branching on no-firmware-declaration must handle no-firmware-requirement and firmware-declaration-unknown; the identification caveats now carry the caller's stated size.
* translate flatpak sandbox spellings so the override chain reaches the host ([#31](https://github.com/danielcopper/emu-atlas/issues/31))
* installation.firmware_status() is replaced by firmware_for_core / firmware_for_system / firmware_inventory / identify_firmware. The packaged table is renamed from data/bios_registry.json to data/firmware_hashes.json and no longer carries declarations; atlas.bios is gone in favour of atlas.firmware. The firmware contract shape changed accordingly.
* `CoreInfo` gains the `options` field and the fixture core spec accepts an `options` map; a new caveat code `card-generation-mismatch` joins the contract.
* the machine seam signatures, health representation, vector schema, and several public names changed; the vector contract's expected blocks are re-serialized under schema 2.
* rebuild atlas as a live resolver — machine seam, rule cards, ES-DE catalogue ([#13](https://github.com/danielcopper/emu-atlas/issues/13))

### Features

* **audit:** add per-game capability to coverage matrix ([#18](https://github.com/danielcopper/emu-atlas/issues/18)) ([97fcb08](https://github.com/danielcopper/emu-atlas/commit/97fcb08362c849ddb7b1fa8bcc6dbc5707e86f6e))
* card applicability by feature detection — the generation question decided on evidence ([#19](https://github.com/danielcopper/emu-atlas/issues/19)) ([96f4b23](https://github.com/danielcopper/emu-atlas/commit/96f4b23259ecffb82eb5f9158635c6d988ffa897))
* **catalogue:** answer where a system's ROMs live and what launches them ([#59](https://github.com/danielcopper/emu-atlas/issues/59)) ([da3909c](https://github.com/danielcopper/emu-atlas/commit/da3909c85031650253cab5b75c83347de70cbc86))
* core-audit wave 1 — opera rule card, triage queue, save-dir card subdirs ([#17](https://github.com/danielcopper/emu-atlas/issues/17)) ([c9479d6](https://github.com/danielcopper/emu-atlas/commit/c9479d6bbf1c5253462d860d853d626d69849d15))
* **evidence:** say when an arrangement has never been seen alive ([#50](https://github.com/danielcopper/emu-atlas/issues/50)) ([3415b3f](https://github.com/danielcopper/emu-atlas/commit/3415b3f17a668f608f3610a30c027a7a540418d2))
* **evidence:** state when the machine moved past the verified version ([#56](https://github.com/danielcopper/emu-atlas/issues/56)) ([1478723](https://github.com/danielcopper/emu-atlas/commit/14787236facba93510828b365b63cbac77017e92))
* firmware answers per emulator — what it wants, where it goes, whether it is right ([#22](https://github.com/danielcopper/emu-atlas/issues/22)) ([a37a616](https://github.com/danielcopper/emu-atlas/commit/a37a6167b4284dfd1e4ce648584f0c70fa48e543))
* **installations:** answer the catalogue question on every handle ([#48](https://github.com/danielcopper/emu-atlas/issues/48)) ([e796de4](https://github.com/danielcopper/emu-atlas/commit/e796de43ed357f2dbb8f2d4fbd0b724f9af5e49f))
* **installations:** ask every installation one question ([#49](https://github.com/danielcopper/emu-atlas/issues/49)) ([c2799c7](https://github.com/danielcopper/emu-atlas/commit/c2799c78c9b04d4d1aec01ab17ef1f346e2e326f))
* **machine:** glob answers how much of the walk it could read ([#47](https://github.com/danielcopper/emu-atlas/issues/47)) ([adb9daf](https://github.com/danielcopper/emu-atlas/commit/adb9daf363cd5048b47497be4b7afe3ffa65c730))
* **placement:** answer where a run's savestates land ([#61](https://github.com/danielcopper/emu-atlas/issues/61)) ([6197893](https://github.com/danielcopper/emu-atlas/commit/6197893118a904f07aec2010b780e92716515ff2))
* rebuild atlas as a live resolver — machine seam, rule cards, ES-DE catalogue ([#13](https://github.com/danielcopper/emu-atlas/issues/13)) ([ae8fdba](https://github.com/danielcopper/emu-atlas/commit/ae8fdbabe0b1137b57e56e1273f62b1726ce3610))
* state the per-game VMU names a live run established ([#43](https://github.com/danielcopper/emu-atlas/issues/43)) ([747759f](https://github.com/danielcopper/emu-atlas/commit/747759fd401bd19f479b5e0476638046ecf0a6db))
* **systems:** atlas's own system vocabulary, and the way to check a name against it ([#58](https://github.com/danielcopper/emu-atlas/issues/58)) ([5b8e34b](https://github.com/danielcopper/emu-atlas/commit/5b8e34be107dd1120ca2d6b5b833012829e53fbc))


### Bug Fixes

* **catalogue:** anchor the ROM directory on the setting the frontend reads ([#60](https://github.com/danielcopper/emu-atlas/issues/60)) ([f8cf0d4](https://github.com/danielcopper/emu-atlas/commit/f8cf0d473820f998471d770af947ff7ce5e197f5))
* **cfg:** parse retroarch.cfg the way config_file.c does ([#37](https://github.com/danielcopper/emu-atlas/issues/37)) ([fa3817a](https://github.com/danielcopper/emu-atlas/commit/fa3817a61ff9d1f274f36e19b9d2ed195293f126))
* **cfg:** resolve the override chain the way RetroArch merges it ([#38](https://github.com/danielcopper/emu-atlas/issues/38)) ([c7a140d](https://github.com/danielcopper/emu-atlas/commit/c7a140d3ca7414861affc2041089589b7e344879))
* **contract:** health findings serialize as the caveats they are ([#51](https://github.com/danielcopper/emu-atlas/issues/51)) ([2099f2a](https://github.com/danielcopper/emu-atlas/commit/2099f2a7105a7e59b689b6f25f3624d652d8439b))
* **esde:** read the per-system emulator selection in both gamelist locations ([#32](https://github.com/danielcopper/emu-atlas/issues/32)) ([831339c](https://github.com/danielcopper/emu-atlas/commit/831339ca596c42802b239b91d9ff12d92a070c2d))
* **firmware:** contain the unclaimed scan and refuse malformed declarations ([#35](https://github.com/danielcopper/emu-atlas/issues/35)) ([2acc9a4](https://github.com/danielcopper/emu-atlas/commit/2acc9a4e78a555d1378a66198ad970bf8da35cd0))
* **firmware:** read core declarations the way RetroArch reads them ([#41](https://github.com/danielcopper/emu-atlas/issues/41)) ([4752beb](https://github.com/danielcopper/emu-atlas/commit/4752beb889fedada57c4cd9494e425ec0a8e6c8c))
* **firmware:** unify the standalone vocabulary and resolve an absent system root ([#53](https://github.com/danielcopper/emu-atlas/issues/53)) ([19e014e](https://github.com/danielcopper/emu-atlas/commit/19e014e0fe9894e3a009e0fac3dd89e21d3b9e05))
* **installations:** every answer states the installation's health ([#52](https://github.com/danielcopper/emu-atlas/issues/52)) ([9764975](https://github.com/danielcopper/emu-atlas/commit/9764975e743401b5aed767a5516fdcaace648cb7))
* **installations:** read each source once and match the game it is about ([#40](https://github.com/danielcopper/emu-atlas/issues/40)) ([b39077e](https://github.com/danielcopper/emu-atlas/commit/b39077ee535c502fee1a72de8aee06fa401af643))
* **machine:** answer every path spelling the way the kernel does ([#46](https://github.com/danielcopper/emu-atlas/issues/46)) ([1fea5df](https://github.com/danielcopper/emu-atlas/commit/1fea5df30a5c0efaa41c8487a99b5fb7ecc5bbb8))
* **machine:** keep the probe's phase-1 answer and reach atlas under vendoring ([#33](https://github.com/danielcopper/emu-atlas/issues/33)) ([014f544](https://github.com/danielcopper/emu-atlas/commit/014f54410f49727a161475fcc7c42696328d900a))
* **placement:** name the content the way RetroArch names it ([#39](https://github.com/danielcopper/emu-atlas/issues/39)) ([2094822](https://github.com/danielcopper/emu-atlas/commit/2094822cc7c005311fcdeed9cbfee35991aaffd2))
* **placement:** resolve the system directory the way the core receives it ([#44](https://github.com/danielcopper/emu-atlas/issues/44)) ([02177f2](https://github.com/danielcopper/emu-atlas/commit/02177f279e02ec9e2fa56ca4330184cd5a6bb1ea))
* **placement:** say when a granularity is unstated, and name what decides it ([#24](https://github.com/danielcopper/emu-atlas/issues/24)) ([3c03908](https://github.com/danielcopper/emu-atlas/commit/3c039084635a8349cf51bca4cd791733f78b88b4)), closes [#23](https://github.com/danielcopper/emu-atlas/issues/23)
* say which kind of empty a firmware answer is ([#42](https://github.com/danielcopper/emu-atlas/issues/42)) ([9bc7612](https://github.com/danielcopper/emu-atlas/commit/9bc7612bdc1a19aa065b45dd90fda47a9fabcf0a))
* **scripts:** validate CLI inputs and reduce validator complexity ([#27](https://github.com/danielcopper/emu-atlas/issues/27)) ([141dfff](https://github.com/danielcopper/emu-atlas/commit/141dfff28766bc86f9b7ce2488b1c0db36c472d4))
* translate flatpak sandbox spellings so the override chain reaches the host ([#31](https://github.com/danielcopper/emu-atlas/issues/31)) ([0f4ddac](https://github.com/danielcopper/emu-atlas/commit/0f4ddac8305591a9468a8e70fc1deb86813d995a))


### Documentation

* add the project mark to the README ([#34](https://github.com/danielcopper/emu-atlas/issues/34)) ([cd94346](https://github.com/danielcopper/emu-atlas/commit/cd943467e4961d40d7cf017c69b9075eda8893cf))
* align DESIGN and README with the implementation, add developer guide ([#30](https://github.com/danielcopper/emu-atlas/issues/30)) ([563a32d](https://github.com/danielcopper/emu-atlas/commit/563a32dfebf3d27dc7b51ab3f1ce88f660701cf2))
* project memory for the live-verification round ([#20](https://github.com/danielcopper/emu-atlas/issues/20)) ([767e324](https://github.com/danielcopper/emu-atlas/commit/767e324dfab2ef31bc34b34c342bb2870572a68c))


### Code Refactoring

* **api:** ask the save questions in RetroArch's own two words ([#62](https://github.com/danielcopper/emu-atlas/issues/62)) ([0447582](https://github.com/danielcopper/emu-atlas/commit/0447582005b44dd764cca0846afb0f8fbed6aa47))
* **api:** one word per concept in the answer grammar ([#54](https://github.com/danielcopper/emu-atlas/issues/54)) ([fefba7b](https://github.com/danielcopper/emu-atlas/commit/fefba7b88597637f0b97e7f1c3899964b216cdac))
* **api:** two tiers, and the arrangement is bare, not standalone ([#55](https://github.com/danielcopper/emu-atlas/issues/55)) ([a415e94](https://github.com/danielcopper/emu-atlas/commit/a415e9416067ce15170663161eed792ee11af7cf))
* structural cleanup — status-model seam, exact vector contract, verified packaging ([#16](https://github.com/danielcopper/emu-atlas/issues/16)) ([a617f1d](https://github.com/danielcopper/emu-atlas/commit/a617f1df0288065d8bf70857958e006686eaa113))

## 0.1.0 (2026-07-21)


### Features

* registry generator and data provenance ([#9](https://github.com/danielcopper/emu-atlas/issues/9)) ([34872e9](https://github.com/danielcopper/emu-atlas/commit/34872e962c1655988a6506e67ce259478711bb04))
* retroarch install detection, save placement and bios registry ([#6](https://github.com/danielcopper/emu-atlas/issues/6)) ([ece9bf1](https://github.com/danielcopper/emu-atlas/commit/ece9bf17a4721f3d71e424f08077b403d05ab9bc))
