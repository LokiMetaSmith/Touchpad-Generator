# Project TODOs

## High Priority
- [x] Add CLI interface for `create_touchpad_pcb.py`
  - Allow specifying input/output filenames via arguments.
  - Allow configuring design parameters (via size, trace width, etc.) via arguments.
- [x] Add error handling
  - Gracefully handle missing input files.
  - Check for invalid DXF content.

## Medium Priority
- [ ] Add Tests
  - Create a dummy DXF for testing.
  - Add unit tests for polygon parsing and logic.
  - Add functional tests to ensure board files are generated correctly.
- [ ] Support odd shapes
  - Investigate the "unintended hard baked assumptions" mentioned in README.
  - Add test cases for non-rectangular shapes.

## Low Priority / Future
- [x] KiCad Support
  - Investigate generating KiCad files directly or via conversion.
  - Downgraded `empty.brd` and `empty.sch` to Eagle 7.7.0 format to ensure compatibility with KiCad's importer (which struggles with Eagle 9.x files).
  - Added `isolate="0"` to generated polygons.
  - This would make the tool usable for a wider audience without Eagle.
- [ ] Improve Performance
  - Optimize polygon processing if dealing with very large touchpads.
- [ ] CI/CD
  - Set up GitHub Actions for linting and testing.
