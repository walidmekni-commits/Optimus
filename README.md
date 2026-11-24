# PLAXIS AI Design Automation

## OPTIMUS® V26.1.9 · revised module interface, part 1 (Dr. Walid's remarks of 23 Nov 2026, A3 design Rev B)

- **Switching between modules:** the module tabs are now large boxes side by side, like the Puzzle, joined by arrows. The
  arrows show chain A (Sensitivity → Optimization → Modification → Scenarios); Calibration (chain B → Modification) and
  Advanced reporting follow. Each box shows the state of its latest study. Hover an arrow to see what it hands over.
- **Study plan (first page of the study page, W6.2):** the study page opens on it. Choose one module, or several modules
  in your own order (start from chain A, chain B or an empty plan; drag ≡ or use ↑ ↓ to reorder). The plan is saved with
  the project, and with a plan the boxes follow its order with step numbers.
- **Design modification, many modification runs (W1.1–W1.3, W1.5, W1.6):** each item takes one or more new values (one
  Puzzle box each). The Runs box asks for the **Number of modifications** (1–20) and how to rank them (least change that
  meets every criterion, or largest governing margin). Modification k takes the k-th value of each item, or tick
  **Match the puzzle** to build each package from the boxes yourself. Each modification can have a name. The results
  show one column per modification with ✓ / ✗, change %, effect, governing margin and rank, and **Choose ★** picks the
  one handed to Design scenarios. The figures and the Word and Excel reports follow: a modifications table, the
  comparison, and a ranking chapter. A study saved before this version (one new value) works as before.
- **What next? (W4.1–W4.3):** a card after the report of every module asks the next question (for optimization, in
  Dr. Walid's words: "Would you like to proceed with Design Modification and Design Scenarios?"). The suggestions can be
  reordered, removed or added, and the order is saved in the Study plan. The order follows the results: Optimization
  moves up when no modification passes, and + Add new runs comes first when an optimization fails or a calibration
  ends at the edge of its range. **Proceed** opens the next module pre-filled; **Pre-fill only** prepares it for later.
- **Handover Optimization → Design modification (W5):** the recommended run becomes Modification 1, the interpolated
  limit becomes Modification 2, the other passing runs become Puzzle boxes, and the targets become acceptance criteria.
  A "Linked input" chip says where the values came from, and **Unlink** keeps them as ordinary inputs. Nothing is
  calculated until Run is pressed. The other arrows open the next module with the results already chosen.
- Still to come (part 2): Report All the Details, Stages to report in Advanced reporting, the Project details screen,
  reuse of runs already calculated, and the full handovers of the other arrows (base scenario, calibrated model).

## OPTIMUS® V26.1.9 (missing PLAXIS features, batch 1: the P1 items for excavation and wall design)

From the QA/QC *Report for Missing Features* (24 Nov 2026). The 13 P1 items, available in every module:

- **Tunnels**: Tunnel objects and contractions (line / surface) are read; the contraction Cref is a variable (per stage when staged); lining thickness and face pressure through the plate material and the face load. New results: the **surface settlement trough volume Vs** (3D: the section at mid y) and the **volume loss VL** = Vs / (π D² / 4) once the tunnel diameter is given.
- **New structures**: an anchor row, a strut level, a plate, a geogrid, an embedded beam (3D: also a beam) drawn from two points (*PLAXIS features of the study › New structures*), with its material, the stage it is active from and an anchor prestress. Each one is a variable (not added / added) with its level shift.
- **Anchor prestress per anchor row and stage**: the anchors of one material form a row; its prestress is offered from the first stage the row is active (Adjust prestress is switched on in that stage).
- **Interface objects**: each interface (positive / negative) is read with the structure it lies along; its own Rinter and virtual thickness factor δinter are variables (a custom interface material copied from its material or from the adjacent soil, so the soil and the other interfaces keep theirs).
- **Structure results**: geogrid (grout body) force, embedded beam skin friction, interface effective normal stress, shear stress and relative shear stress, 3D beam (capping beam) N, M2, M3.
- **Water level per stage**: a horizontal phreatic level over the whole model per stage (dewatering steps), and the water condition of each soil cluster per stage (Global level, Dry, Interpolate; up to 60 clusters).
- **Groundwater flow calculation**: the pore pressure calculation type of each phase (Phreatic, Steady-state groundwater flow, from the previous phase); results: max specific discharge |q|, groundwater head, steady and active pore pressure.
- **Wells and drains**: read with their discharge / head; each stage's well discharge and drain head are variables.
- **Drainage type**: Drained, Undrained (A), (B), (C), Non-porous per soil material (short / long term).
- **Adding a safety phase**: *Factor of safety per stage* adds a safety phase after each ticked stage in every run (Run 0 included) and reads ΣMsf for each.
- **Design approaches**: a variable per run: characteristic, EC7 DA1-C1, DA1-C2, DA2*, DA3. OPTIMUS applies the partial factors itself (variable loads × γQ, tan φ' / γφ', c' / γc', su / γcu; DA1-C1 and DA2*: structural forces × 1.35 on the effects), listed in the run's soil_models.txt.
- **Results at chosen points**: points typed or taken from the model (e.g. a building corner) give |u|, ux and uz (2D: uy) at the point; with *Results over time*, at every stage.
- **Dynamic loads**: a dynamic phase after a chosen stage, harmonic, on a chosen load or as a shaking of the model base, with viscous boundaries; amplitude, frequency and duration are variables; results: ax, vx and ux in the dynamic phase. Needs PLAXIS with the Dynamics module.

**In every module** (Sensitivity, Optimization, Scenarios, Calibration, Design modification, Advanced reporting): the new inputs are variables in section 2 (and Puzzle boxes), the new results are control parameters, targets, measured results, acceptance criteria and reported results in section 1 (Sensitivity Detector included); the factor of safety after each ticked stage is a main result of its own (summary, charts, comparison, statistics). The Word and Excel reports list the study's PLAXIS features, the interfaces / tunnels / wells / drains of the model, the drainage type of each soil and what each run set in PLAXIS (sheet *PLAXIS features*); the added phases are named (safety phase after …, dynamic phase). The 3D / 2D view and the report's model geometry draw the new structures (translucent / dashed, "added"), the result points and the wells and drains; a run's details list what it set in the stages.

Also: what each run set in the stages is kept in stages_applied.txt; a variable with fewer values than runs (e.g. not added / added in 4 runs) spreads its values over the runs (it stopped the study with an error). The full run of the Original File reads the same results as V26.1.8 (the new results are chosen in the studies).

**Through the PLAXIS scripting API, with limits**: earthquake records (table signals) are not in this batch (harmonic only); tunnel round length and support delay are not variables (the Tunnel Designer's excavation sequence is not reachable as single values through the scripting server); the total inflow into an excavation through a cross-section is not given by the scripting server (the max specific discharge and the well discharges are). The PLAXIS commands were written from the PLAXIS 2D / 3D reference manuals and checked with a simulated PLAXIS; each feature needs one check on PLAXIS 3D 2024.3 / 2025.1 (QA/QC).

## OPTIMUS® V26.1.8 (QA/QC review of the five study modules)

- **PLAXIS 3D results are read again**: PLAXIS Output is opened with the security it really has (secured or not); before, a secured request to an unsecured Output server failed on every result ("Syntax of request could not be understood") and the study still said "succeeded". PLAXIS 3D 2024 names are used (wall axial force `Plate.N11`, anchor force `NodeToNodeAnchor.N`), with alternatives for other versions.
- **Factor of safety only with a safety phase**: a model without one no longer reports "FoS 1"; the result is left out (reporting, comparison) or the study asks for a safety phase (optimization, calibration).
- **No retry of a deterministic PLAXIS error** (invalid material, unknown property…): it was calculated 4 times.
- **Run 0 from the full run of the Original File**, matched by result and stage; a result the model does not have no longer forces Run 0 to be calculated again.
- **Results that could not be read** are listed with their reason (summary, key findings, page).
- **Design optimization**: the limit of each target is interpolated between the runs on either side of it (governing limit); the recommended run is the least over-designed one on every target (governing margin); *+ Add new runs* places the next run at the limit.
- **Design modification**: Run 0 against the modified run by number (not first / last), each result improves / worsens, optional acceptance criteria and a verdict.
- **Design scenarios**: *All combinations* (scenario matrix of every value of each variable) and the governing scenario of every result.
- **Calibration / Back-analysis**: residual of each measurement in the best run, the calibrated value from the misfit curve (parabola through the best run and its neighbours), a warning when the best fit is at the end of the range, and *+ Add new runs* refines around it.
- **Advanced reporting**: every chosen result at every stage (table), no "in every scenario" figure title, no FoS without a safety phase.
- **Time in every module**: *Results over time* gives each stage a date (days from the start of works) and adds a long-term consolidation phase after the last stage (e.g. to day 365); every chosen result is read at every date, with a time-history figure. Calibration takes a field time series (day, value) as its benchmark.
- **Soil model as a variable** (every module): MC / HS / HSsmall / SSC per material; the new model's parameters come from the existing ones (SSC: λ* = pref / Eoed,ref, κ* = 3 (1 − 2 νur) pref / Eur,ref, μ* = λ* / 25), saved per run in soil_models.txt. Also the SSC creep index μ* and a stiffness set × factor (E50, Eoed, Eur, G0 together).
- **Soil parameter list**: classification and numerical internals hidden (ClayFraction, cK, eMax, Kwn…); PLAXIS 3D spellings get their units (E50Ref, cRef, pRef: kN/m²; permeability m/day).
- **Optimization pass / fail**: a run passes only when every criterion is met (a 99.99 % achievement shown as 100 % no longer counts).

## OPTIMUS® V26.1.7

- **The surcharge area follows its corners**: changing a corner of a line or surface load, or a model point that sits on one of its corners (for example Point_12 or Point_13 next to the wall), changes the load's area (or length). The new area is shown for each value in the view (Area tab and a blue dashed outline in 3D), under the variable input and in the scheduled runs. In PLAXIS the load corner moves together with the point.
- **Variable input criteria as Point, Line, Area, Volume**: the Geometry tab lists the variable inputs under these four headings. The surcharge area, its edges and corners are geometry (Area in 3D, Line in 2D); the load values stay under Loads.
- **Puzzle**: geometry boxes are grouped as Point, Line, Area, Volume. A corner or point of a surcharge load is shown as its area with the changed coordinate in brackets, e.g. "SurfaceLoad_1_1 area: 175 m² (C3 y: 15 m)". In a run, the area is the result of all the boxes of that load in the run.

## OPTIMUS® V26.1.6

- **Surcharge area as one puzzle box**: the whole area (m², 3D) or length (m, 2D) of a line or surface load is one variable input (for example 120, 160). It is scaled from the corner next to the wall, which stays in place. The edges and each corner can still be varied one by one.
- **Coordinates editable in the view**: the x, y, z of every point and of every load corner can be typed directly in the visual interface (a list such as -20, -22 makes several values). The change appears at once in the variable inputs and the Geometry summary, and a change made there shows in the view (blue labels with the new values).
- **Geometry as Point, Line, Area, Volume**: in the visual interface (tabs) and in the Geometry summary of the variable inputs. Clicking a point in the view opens its row.
- **🧹 Clear All**: on the scheduled runs, the puzzle runs, the variable inputs, the control parameters and the ticked items of the view. A second click confirms.
- **Puzzle in both places**: the puzzle stays at the bottom of the variable input box and also appears next to the scheduled runs.

## OPTIMUS® V26.1.5

- **More than one variable input (Sensitivity Analysis)**: OPTIMUS stops proposing the runs and asks "🧩 Match your Puzzle for the First Run!". The puzzle boxes move next to the scheduled runs (each side scrolls). The total number of runs stays editable: it sets the number of run boxes (Match similar fills them from Run 1).
- **Current analysis** shown above Control parameters.
- **Failed runs**: Failure Reason and Proposed Solution (from the PLAXIS error and LogInfo code, with the input values at the end of their range), in the results, the report and Word.
- **After the first runs**: the effect of each variable input on each control parameter (change over its range, share, direction to reach the criterion, value that reaches it) and proposed optimized combinations (least change, largest margin, balanced) with "＋ Add to the runs".
- **Report**: no one-line chapters. A chapter "Calculation quality and checks" (runs, attempts, geometry checks, model check, saved images, failed runs). Key findings under the conclusion. Model output / mesh chapters only with their images.
- **Surcharge area**: the edges (x / y min / max) and every corner of a line or surface load can be varied, also when its corners are not model points (3D). Shown in the Loads table of the geometry view and in the 3D view. In PLAXIS the corners are moved, or the load is drawn again with its values and stages.
- **Summary tables**: "Insert proposed values" with ＋Add (variable inputs, soils, control values).
- **Model Output**: Export More Output Results (run and items: displacements, excess pore pressures, wall moment / shear / axial force, mesh), Last run only / All runs, share all images (zip), zoom and rotate on click. No rainbow icon.
- **First page**: Geotechnical / Structural Engineering as icon tiles. The desktop icon opens on the sign-in page, and the requirements are installed only the first time (faster start).

## OPTIMUS® V26.1.4

- **First page**: choose Geotechnical Engineering or Structural Engineering (shown in the header and on the report).
- **Full PLAXIS run reads every stage**: each main result (safety, displacements, structural forces, pore pressure) in every phase of the Original File (the factor of safety in the safety phases).
- **Control parameters · Summary table**: the main results of the Original File by stage, for the chosen category (Safety, Displacements, Structural forces, Pore pressure). Tick a row to add it as a control parameter. Missing values are flagged (⚠), items the model does not have are marked "not in this model".
- **Variable inputs · Summary table**: follows the tab (Soil, Structures, Loads…); soils as parameters × materials. Tick a value to add it as a variable input. A model check lists what is missing (no loads, a load without value or not active in any stage, a structure without material…).
- **Visualization**: the run views are hidden until "Show the run visualization" is ticked. A table of the loads and structures sits inside the geometry view; ticking one colours it (magenta).
- **Report & figures**: one section. "What do you prefer to visualize?": Figures, Tables, Model Output (PLAXIS Output contour plots of every run: total / horizontal / vertical displacements, bending moment), Model Mesh, Model Geometry. The report has a cover, the main inputs (soils, structures, loads, stages, model check), the comparison of every run with the Original File, numbered tables and figures; the Word report the same, with header, footer and page numbers.
- PLAXIS Output images are exported through the Output scripting server (best effort: an image that cannot be made is skipped and listed as missing).

## OPTIMUS® V26.1.3

- **Opening page**: OPTIMUS® with "Engineering Optimization Engine" shows for a moment when the app opens (click to skip).
- **Logo**: a plain O with the three ground lines (no tail any more), in the header, the opening page, the window icon and the desktop shortcut (static/img/optimus.ico).
- **Studies of each tab**: under the tabs, "＋ Add New Sensitivity Analysis" (or Optimization Study, Design Modification, …) starts an empty study. On the right, the list of that tab's studies: click one to open its setup and results, 🗑 twice to delete it (with its run files). Each tab shows its own latest study.
- **Total number of runs**: always editable. With the proposed values given one by one, a new number adds values (same step) or removes the last ones, and the box shows the real total.

## OPTIMUS® V26.1.2

- **Launcher fixed**: run.bat no longer shows "Windows cannot find '-m'" (the pythonw path was read too late inside a batch block).
- **Run 0 is not run again**: it takes its results from the full PLAXIS run of the Original File. The button counts only the new calculations ("Run 4 PLAXIS calculations").
- **Match the puzzle**: when ticked, no runs are proposed and no errors are shown, just "Match your Puzzle!" until boxes are dropped into the runs.
- **Parameter card**: Values comes before Range; each value is its own box with × to delete and "＋ Add value".
- **Figures by run**: at least 3 figures with Run 1, Run 2, Run 3… on the x-axis. Pick what each figure shows on the y-axis; a dashed line marks the Original File (Run 0), and a summary below gives the lowest and highest run and their change against the Original File.
- **Header**: OPTIMUS is centered above the tagline and the ® is larger.

## OPTIMUS® V26.1.1

- **Its own program window.** `run.bat` (and the desktop shortcut) opens OPTIMUS in its own window: Edge or Chrome in application mode, found through the Windows registry, with no address bar and no tabs. The server runs without a console window (`pythonw`). Opened in a browser tab instead, OPTIMUS shows **Open as a program**.
- **The full PLAXIS run of the uploaded model is remembered.**
  - The run also reads the model (geometry, materials, phases) while the file is open in PLAXIS, so the study page needs no second reading.
  - The run is remembered by the PLAXIS file's content (SHA-256). The same file uploaded again (a new project, a restart, an update) is not calculated again. A changed file is calculated again.
- **No dead buttons.** Without the model, *Add control parameter* and *Add parameter* say so and read the model from PLAXIS.

## OPTIMUS® V26.1

- **Your work is never lost.**
  - Every project is listed under **Open a project** on the first page, straight from the database. Before, the list came from the window's memory, so a new window showed nothing.
  - When the app folder is synced by OneDrive (e.g. on the Desktop), the live database is kept in `%LOCALAPPDATA%\OPTIMUS`. The existing work is copied there the first time.
  - The server writes a log (`data\logs\optimus-server.log`) and restarts itself after an error.
  - Closing the window no longer stops the server. It stops by itself after 20 minutes with no window open and no run in progress.
  - Runs cut short by a shutdown are shown as stopped. **Restart** continues them.
- **Save / Save As / Open / Close** (also Ctrl+S, Ctrl+Shift+S, Ctrl+O).
  - Projects are saved to `Documents\OPTIMUS Projects\<Project>\<Project>.optimus` with organized folders: `Model`, `Basic Model Run`, `Sensitivity Analysis\Study 01 - <date>\Run 003\…`, `Design Optimization`, and so on, with each study's `results.xlsx`.
  - When saving, choose the PLAXIS files: **all runs**, **specific runs** (e.g. `3, 5-7`) or **none**.
  - Closing asks: Save, Save As, Close without saving, or Cancel.
  - Each run in the results has **Open** (opens its PLAXIS file in PLAXIS), 📁 (shows it in its folder) and ⬇ (download).
- **Sign-in.** Create an account with a password (a strength meter, at least 8 characters with letters and digits). You get a one-time recovery key.
  - **Forgot password** emails a 6-digit code (set up the email under ⚙ Settings), or uses the recovery key.
  - You can change the password, sign out, or turn sign-in off.
- **Units.** Choose **Same units as PLAXIS** (read from the PLAXIS project) or **Custom units**: displacements, forces and moments, lengths, stresses and time, with an optional **scale** for each.
  - Results, control parameters and reports are converted. The inputs stay in PLAXIS units.
- **Sensitivity Detector on the control parameters.** Each control parameter shows the input it is most sensitive to.
  - The detector's ranking of the most affected results has **Use the top results as control parameters**.
- The logo reads OPTIMUS®.

## The uploaded model runs once

- The basic model run (step 2) is calculated **once**. It is never retried automatically. Before, a run that failed while reading one result was repeated up to 4 times, which looked like the same file running again and again. To repeat it, press **Run PLAXIS again**.
- If the model does not have a result (no plates, no anchors…), that result is shown as "—" and the run still succeeds.
- When the run finishes, a green badge appears: **Uploaded PLAXIS File Run Successful!**

## The pages (since 13 November 2026)

Two screens, nothing repeated:

1. **Home.** The project (title, engineer, date, PLAXIS 2D / 3D, the PLAXIS model: upload the project file and its
   data folder, a file on this PC, the project open in PLAXIS, or the example) and the four studies as tiles:
   **Sensitivity analysis, Design optimization, Design modification, Design scenarios.** Recent projects are listed.
2. **Study.** The four studies are tabs at the top. Under them:
   - **Geometry**, the model read from the PLAXIS file in a rotatable window (three.js: drag to rotate, right-drag to
     move, wheel to zoom; 3D / Front / Side / Top; point names on demand). *Read from PLAXIS* and *Import geometry*
     (a .p3d / .p2dx, a zip, a data folder or a model.json) are in its bar. Hover shows coordinates; click a point,
     line, structure, load or soil layer to see its coordinates and PLAXIS parameters on the right. Type a new
     coordinate (or a whole-object move Δx / Δy / Δz) and press Enter, or click a parameter chip: it goes into the
     study. Items in the study are orange.
   - **1 Variable input criteria**: domain (Geometry, Structures, Loads, Soil, Water conditions, Mesh, Stages) ›
     parameter › min, two-handle slider, max, with the unit. Mesh levels are buttons. With *Customize*, one box per
     run. In a design modification: the existing value → the new value.
   - **2** Sensitivity: **control parameters** (domain › PLAXIS result › Fixed value or Range). Optimization:
     **target optimization criteria** (domain › result › ≥ / ≤ › value). Modification and scenarios: the results to
     compare.
   - **3 Runs**: the total number of runs (Run 0 included, shared between the parameters), Even or Customize,
     Run 0 = existing design, and the scheduled runs in one table of three columns: Run | Parameter values | Result.
   - **4 Results** (after *Run*): a summary (optimization: *OPTIMIZATION ACHIEVED*, or *NOT ACHIEVED* with the % of
     the targets reached), the same three-column table with the results, the figures, and **Excel** and **Word
     report** downloads (always both). Sensitivity: *Swap axes* (default as sketched: the variable on the y axis, the
     control parameter on the x axis). Optimization: *+ Add new runs [n]* adds runs in the gaps between the earlier
     ones; earlier runs are kept, not calculated again, and the new ones are highlighted.

**PLAXIS fixes and controls (13 November 2026, after the first PLAXIS 3D 2024.3 test):**
- Load values (e.g. `Surcharge.sigx`) are staged in PLAXIS: in Staged construction `setproperties Surcharge "sigx" 22.5`
  fails with *Unknown property*. The value is now set in Structures mode and in every phase where the load (or its
  staged pieces such as `Surcharge_1`) is active (`set Surcharge_1.sigx Phase_n 22.5`).
- Every phase is marked for calculation (`Phase.ShouldCalculate = True`) before `calculate`, so a saved project no
  longer stops with *No phases have been marked for calculation*.
- **■ Stop** while the runs calculate (the run in progress finishes; PLAXIS is not cut in the middle of a phase) and
  **↻ Restart** (the stopped and failed runs) or **Run all again**. Figures, Excel and Word cover the runs made so far.
- The variable selection is above the geometry. Lines are numbered in the view and named
  *Line 5 (Point_10 – Point_11)*; walls, anchors and loads likewise. Clicking a point, line, wall, anchor, load,
  the water level or a soil layer shows its coordinates (whole-object move Δx / Δy / Δz and each end point) and
  its PLAXIS parameters, each with a **+** to make it a variable.
- **🖱 Select from the visualization interface** (in the variables box): while it is on, every item clicked in the
  geometry is added to the variables at once: a point → its level (y in 2D, z in 3D); a line, wall or anchor → a
  move of the whole object (sideways for a vertical one, up or down for a horizontal one); a load → its value; a
  soil layer → its friction angle; the water level → the groundwater head. A message confirms each one, in the
  variables box and in the view. The **+** buttons in the panel on the right add any other coordinate or parameter.
- **Select geometry** (under the 3D view, 16 November 2026): *Select items between two points* (two point lists; click
  the points in the view, or click a line to take both ends; 🎯 picks the list to fill) or *Select items with
  multi-points* (+ add point / − remove). *Select similar items* also takes every other set of points in the model with the
  same shape (the same vectors between the points), whatever they are: anchors, piles, plates, loads, geometry
  lines or plain points, moved anywhere or mirrored left / right (and front / back in 3D). *Whole piles / beams*
  makes a moved point carry its whole pile, so a spacing applies over the full pile length. **+ Add selected item**
  puts it in the *List of selected geometry* (purple in the view; pink while it is being selected) and in the
  variables: *spacing* (the distance between the two points; in a row of similar spacings pile k moves k times the
  change, so the spacings stay equal) or *shift in x / y / z*. The round bullet deselects it (and removes the
  variable). PLAXIS refs are written as `Point_9.x+*0.8~6` (move by 0.8 × (value − 6)); see app/geosel.py.
- `SIM_RUN_SECONDS=1` makes each simulated run take a second, to try Stop / Restart without PLAXIS.

**Corrections 2 (16 November 2026):**
- **Scale** in the geometry bar: x and vertical 0.5× to 5×, y (3D only) Auto or 1× to 5×. In 3D the y axis is stretched
  automatically when the model is much shallower in y than in x (up to 5×), so the piles and anchors are not
  squeezed together. The choice is remembered in the browser.
- **One Variable input criteria box with tabs** (Geometry, Structures, Loads, Soil, Water conditions, Mesh, Stages,
  each with its count). Anything clicked in the view goes straight into the list under the right tab. On the
  Geometry tab a single bar sets what a click adds: *the item*, *items between two points* or *items with
  multi-points*, with *Select similar items* and *Whole piles / beams*. The separate inspector beside the view and
  the *List of selected geometry* under it are gone; a selected group is one row in the list (label, "+N similar",
  spacing / shift in x, y, z) and the × removes it and its purple highlight.
- **Number of runs is the total**: 7 runs with Run 0 and three parameters gives Run 0 plus 2 runs per parameter.

**OPTIMUS as a desktop program (18 November 2026):** *run.bat* starts the server in a minimized window, waits
until it answers, and opens OPTIMUS in its own window (Microsoft Edge or Google Chrome in app mode: no address bar,
no tabs, the OPTIMUS icon). Closing the OPTIMUS window stops the server. Without Edge or Chrome it opens in the
normal browser. *create_shortcut.bat* puts an OPTIMUS shortcut with its icon (static/img/optimus.ico) on the
desktop and in the Start menu. *update.bat* installs a new version: it takes the newest
plaxis-ai-automation*.zip from Downloads (or the zip dragged onto it), replaces the program files and removes the old
ones, and keeps your projects, settings and PLAXIS password (data folder, automation.db, .env).

**Progress bars (18 November 2026):** step 2 and the results of every study show a progress bar with its
percentage (the steps of the run in progress count too: connecting, opening, meshing, calculating, reading the
results) and end on "✓ Run completed · 100 %" ("Runs completed (n of n)" for a study). The uploaded files stay
listed in step 1 after the upload ("Uploaded: … ✓"). The connection button reads "Connect PLAXIS" / "not
connected yet" instead of "Simulated (no PLAXIS)".

**Basic run: progress, one PLAXIS job at a time, cancel (18 November 2026):** reading the model and the runs no longer
use PLAXIS at the same time (one lock for both; before, uploading on the first page started reading the model while
the basic run opened the same file, and PLAXIS could hang). Step 2 shows what the run is doing (connecting, opening
the file, meshing, calculating every phase, reading the results) with the time in the step and in total, "Waiting:
PLAXIS is busy with …" when another job holds it, a *Cancel* button, and a time limit (PLAXIS_BASIC_TIMEOUT_MIN,
180 min by default) after which the run is marked failed with what to check.

**Step 2 required, real PLAXIS (18 November 2026):** *Run PLAXIS on the uploaded model* (first page, step 2) is
now required before a study can be chosen: the study tiles stay locked until the uploaded model is calculated.
The run always uses PLAXIS (never the simulation): when PLAXIS is not reachable the connection window opens (1 ·
paste the password), and *Connect and run the uploaded model* saves the password, switches the app to PLAXIS and
calculates the model completely (every phase). Only the example model (no PLAXIS file) is simulated, and says so.
Connect in the connection window now also switches the calculations to PLAXIS. The OPTIMUS name is a clean
wordmark (logomark with soil layers, letter-spaced name, "Geotechnical design automation" under a rule).

**Sensitivity summary figures (18 November 2026):** a sensitivity analysis now ends (and, live, fills while PLAXIS
calculates) with a *tornado* chart per control parameter: each variable parameter from its largest decrease to its
largest increase of the result against the existing design (%), with the input values, most influential first; and,
with several control parameters, a bar chart of the spread of every control parameter by parameter. Both are in the
figures, the Report (table), Excel and Word. The live line figures are now one per parameter and control parameter
(its own runs and Run 0), since each parameter changes alone.

**Corrections 9 (18 November 2026, handwritten notes):**
- **OPTIMUS**: the software is now called OPTIMUS (centred name in the header, page title). The page background is
  the soil-layers picture (static/img/optimus-bg.jpg), darkened so the text on it stays readable; the boxes keep
  their solid colours.
- **Run PLAXIS on the basic model from the first page** (card 2, no visualization): *🔑 PLAXIS connection* opens the
  connection (1 · paste the PLAXIS password, 2 · automatic) and stays on the first page; *▶ Run PLAXIS* creates the
  project if needed and calculates the PLAXIS file as it is, once, reading every PLAXIS result (FoS, displacements,
  wall forces, anchor force, pile force, pore pressure). The table shows them. API: POST / GET
  /api/projects/{id}/basic. This run is not shown as the results of a study.
- **Control parameter: current value and target**: each control parameter shows the *Current* value from the basic
  model run (e.g. "Current Wall bending moment 280.4 kN·m/m") and the *Target*: Fixed, Range, Max (maximum allowed)
  or Min (minimum required). Max / Min are drawn as "maximum 520" lines and written in the reports. Targets and
  measured values (optimization, calibration) show the current value too.
- **Sensitivity Detector (results)**, next to the control parameters: tick *🔎 Sensitivity Detector* and choose the
  fields (results, by domain; all by default). Every chosen result is read in every run; the results page ranks the
  10 most affected ones (spread over the runs) with the input that moves each one most, live while PLAXIS
  calculates, in the Report, the Word report and a figure.
- **Sensitivity Detector (inputs)**, in the variable input criteria: choose Structures, Loads, Soil or Geometry
  (the items selected in the view, shifted by a step in m), the change (+20 % by default) and tick the items. Run 0
  plus one run per item, each changed alone; the results rank the items by their effect on the control parameter
  (live, Report, Word, figure). *Use the top items as variable inputs* adds the best ones to the variables and
  closes the detector.
- **Deselect**: clicking an item already selected in the geometry view (or a picked point) deselects it.

**Corrections 8 (18 November 2026):**
- **Sentinelion branding**: the Sentinelion logo (static/img/sentinelion.jpg) is the name of the software, centred in
  the header; the old logo, the title and the soil picture are gone (Home, PLAXIS connection and A−/A+ stay small
  on the right). The page background is dark with the matrix digit columns of the logo (static/img/matrix-bg.png);
  the boxes keep their own solid colours, so nothing on them is harder to read. (Replaced in Corrections 9 by OPTIMUS and the soil-layers background.)
- **Two new studies** next to the four: *Calibration / Back-analysis* (uncertain inputs varied together; each result
  gets a measured value; every run gets its misfit, the root mean square of the relative differences, and the best
  fit is named; misfit figure; measured lines on the figures) and *Advanced reporting* (the existing design is
  calculated once, Run 0, and every chosen result is reported in full).
- **Report** card at the bottom of the analysis page: conclusion, project and model, study definition (ranges, lists,
  frozen values, criteria or measured values), every run with every input and result (✓/✗ against targets, misfit,
  achievement), statistics (minimum, maximum, mean, spread with their runs), comparison, geometry checks; Print / PDF,
  Word and Excel. The Word report has the same statistics, calibration and every-run tables.
- **Live figures** under the runs, redrawn at every poll while PLAXIS calculates: the first figures put the variable
  inputs on the x-axis (several inputs grouped in one description, e.g. "φ' / E50ref" with "22 / 16000" ticks) and
  each result on the y-axis; finished runs are blue points, Run 0 an orange diamond, the run in progress pulses and
  the runs still awaiting are grey circles. *+ Add: Visualization Dynamic Results* adds a figure with any x-axis
  (inputs together, one input, run number or another result) and y-axis; added figures are kept with the study.
- **Live figures keep updating (fix)**: before, one failed results request during the runs (e.g. while PLAXIS was
  busy) stopped the page asking for new results, so the figures froze after a run. Now the page keeps asking (with a
  longer pause after a failure, "connection lost, trying again"), asks again at once when you come back to the
  page from PLAXIS, and a drawing problem in one part no longer stops the others. The line above the figures says
  which run is calculating, a retry after an error, and the time of the last update.

**Corrections 7b (17 November 2026):**
- Run cards: *✕ Hide the sketches* (also ✕ on each sketch) keeps the cards with their values; *✕ Hide the run cards*
  hides them all. Both are remembered.
- *● All phases blue* (Geometry header): marks every phase of the project open in PLAXIS Input for calculation, as
  blue in the Phases explorer. Each run of the app already marks every phase before it calculates, so a project saved
  with all phases green (calculated) is always calculated again in full.

**Corrections 7 (17 November 2026):**
- **Real names**: a picked two-point item is named by its geometry line(s) found by place (also when PLAXIS keeps a
  second point at the same place, and chains such as "Line 11 + Line 12"), with the structure on it; "Segment" is gone.
- **Attached items always follow** ("Whole piles / beams" removed; old studies are switched over) and **every point at
  the same place moves together**. This was the cause of "Lines overlap: Line_12, …" in PLAXIS: the end of the free
  length moved but the second point where the grout body starts stayed, so the grout became 11 m instead of 7 m.
- **Every run is checked before PLAXIS** (app/geocheck.py): lines that overlap after the change, no length left, an
  item turned over (errors: Run is blocked, the run is red in Scheduled runs and on its card), an item whose length
  changes through an attached point although no variable sets it (warning).
- **🔒 Freeze the values** on each variable: the values of the runs are kept (run count, puzzle and edits in the run
  view no longer change them; an edit then changes that run only).
- **Geometry for runs › ✥ Edit**: drag a yellow handle like a grip in AutoCAD to stretch / shorten (length, spacing =
  squeeze) or shift (x, y, z). The value goes back to the variable box (the end of the range, or the value in the
  list) and to Scheduled runs; a frozen variable or a value inside the range changes that run only. The value boxes
  under the run view are gone (Confirm, Delete, the checks stay).
- **Hide** on Geometry from PLAXIS, on Geometry for runs (the other takes the full width; "Show …" brings it back) and on
  the run cards. Each view has its own toolbar; *Read from PLAXIS / Import geometry / Example* sit apart in the card
  header.
- **Run cards**: a flat sketch with its own projection (seen along y or x, whichever shows the change), zoomed on what
  moves, numbered badges that never cover each other and the list of changes under it.
- **↻ Re-run from Run n** under the results: the runs before it keep their results (while calculating it stops after
  the run in progress first).
- Picking one point with *Select similar items* takes the points of the same kind of item at the same level, not every
  point of the model.

**Corrections 6 (17 November 2026):**
- **🧩 Puzzle** (Variable input criteria): every variable as a row of boxes, one per decided value ("NodeToNodeAnchor_1:
  10.7 m | 13.4 m | 16.1 m"), grouped under Geometry, Structures, Soil… About one box per run to start with; edit a box,
  ＋ adds one, × removes one (a number variable's boxes are its list of values).
- **Match the puzzle** (Scheduled runs): Run 1, Run 2, … are drop zones. Drag boxes into them, or click a box and then
  a run; one value of an item per run, × takes a box out. *Match similar for other runs* fills Run 2, Run 3, … from
  Run 1 with the next box of each item. The total number of runs follows the puzzle. Off again, the lists / ranges
  decide.
- **Geometry colours follow the tab**: Geometry = points and lines coloured, the rest grey; Structures = structures
  coloured, the rest grey (Loads, Soil, Water the same way). The selection and study colours still show.
- **+ Add parameter** opens a list of the tab's parameters with a search box, their group (e.g. Plate material ·
  DWall) and the current value; click one to add it.
- **Run cards** (Summary of the runs): edit each value, confirm, ◀ ▶ switch a run with the one before / after
  (the order is kept), Show, delete.
- **Customize** in the Runs box starts with the values of Even, so the runs stay the same until a value is edited.
- Values ≥ 1000 are written in full (18700 kN/m², not 1.87e+04).

**Corrections 5 (16 November 2026):**
- **Similar items are counted in full**: picking Line 14 with two similar lines gives "Line 14 and similar (3 items)",
  never "(+N similar)". A copy may not share a point with an item already taken (the mirror of a line about its own
  end was counted as a fourth item); studies saved before are cleaned when opened. Unnamed two-point items show as
  "Segment".
- **Proposed values as a list**: each variable has *Range | Values*. Under *Values* type the values separated by
  commas ("9, 11, 13, 15"): one run per value. The total number of runs and the Scheduled runs follow at once
  (sensitivity: Run 0 + one run per value + the runs of the parameters that keep a range; the other studies: the
  longest list). For a group of similar items the values are for the picked item; the others change by the same
  amount (or within their own range).
- **PLAXIS connection**: option 1 is *Paste the PLAXIS password* (Expert › Configure remote scripting server › Start
  server, then paste the password), option 2 *Automatic connection*. Opening a project when PLAXIS is not reachable
  shows these two buttons (plus Import / Example) and no error; the details are folded under *Details*.
  *Read from PLAXIS* gives the same two buttons; a wrong password says so in one line ("paste it again"). The
  page is never cached, so a new version shows at once.
- **Header**: replaced in Corrections 8 by the Sentinelion logo.

**Layout from the sketch (16 November 2026):** the study page now reads top to bottom as
1 **Control parameters** | **Runs** (total number of runs, distribution, Run 0); 2 **Variable input criteria** |
3 **Scheduled runs** (Run | variable, e.g. "Line 2, Line 6, … (similar) length" | value with its unit, editable to
change that run | ✓ confirm | 🗑 delete; ← Previous / Next →, Confirm all, Restore deleted runs, Run);
**Geometry from PLAXIS** | **Geometry for runs** side by side (the run view shows the point names of the items that
change only, "All names" shows the rest; its value, Confirm and Delete are under it and edit the run directly);
then the **Summary of the runs** (compact cards).

**Corrections 4 (16 November 2026):**
- **Geometry check in PLAXIS**: after the variables are set, every moved coordinate is read back from PLAXIS; if
  PLAXIS did not take it the run stops with "PLAXIS did not take the new geometry: Point_8.x is …". The values
  read back are saved with the run (geometry_applied.txt in the run folder, and "Geometry applied in PLAXIS" in
  Results › Individual runs). Re-meshing follows any geometry change.
- **Every item on its own row**: a group picked in the view lists each item ("Line 2 (NodeToNodeAnchor_1): Pt 1
  [Pt 7] – Pt 2 [Pt 8] | original length 13.42 m | proposed range min – max" with a slider). Each item can have
  its own range; in a run every item takes the same fraction of its range, so the group still changes in one run.
  🗑 deletes one item of the group.
- **Delete**: 🗑 Delete on every variable and control parameter; 🗑 on each proposed run (↺ Restore brings them back);
  **🗑 Remove an item in a run**: a structure or load (deactivated in every stage), a point (deleted with its lines)
  or a stage (deleted; only a stage without later stages), with the levels keep / remove.
- **Visualize proposed runs** opens a second full 3D view (rotate, pan, zoom, Fit, 3D / Front / Side / Top, Names)
  showing the proposed run: changed items in blue with their new lengths, the existing ones dashed.
  ← Previous / Next → switch runs; Confirm / Customize / Delete for the run shown; the compact cards stay below.
- **■ Stop and report the results so far**: the run in progress finishes; the table, figures, Excel and Word cover
  the runs done; Restart calculates the rest.

**Runs: one change of a group = one run (16 November 2026):**
- Clicking a pile, anchor, beam, wall or line makes its **length** the variable (the upper end, where it meets the
  wall or the raft, stays; the other end moves along the item). With *Select similar items* the same length applies
  to every similar item **in the same run**: "NodeToNodeAnchor_1 (+3 similar) length = 16 m" is one run with all four
  anchors at 16 m. *Shift in x / y / z* and *spacing* stay available in the row's menu.
- Points of attached structures that are not changed themselves follow the change: the grout body (geogrid) at the
  end of a lengthened anchor moves with the anchor end and keeps its length (app/geosel.py `follow`).
- **👁 Visualize proposed runs** (under the view): a summary of the runs (Run 0; Runs 1–2: … = 10.7, 16 m; …) and one
  card per run with a sketch of the system (grey dashed: existing; blue: the run, with the new lengths "16 m ×4
  (was 13.42)"), **Confirm** or **Customize** (type another value for that run), and *Show in the view* (the run
  drawn in the 3D view with its lengths). *Confirm all* confirms every run.
- Control parameters: **Max / Min / Avg** next to Fixed / Range (the maximum, minimum or average magnitude of the
  result over its nodes; PLAXIS ref suffix `#min` / `#avg`).
- Results: **All runs** (table and figures) or **Individual runs** (one run: what changed, its results, its sketch,
  Show in the view, previous / next).

**Corrections 3 (16 November 2026):**
- *Select similar items* acts at once: ticking it turns the last item picked into a group with all its similar items,
  listed under the boxes (purple in the view, ✕ removes one item); unticking it keeps only the picked item.
  *Whole piles / beams* likewise updates the last group at once. Clicking an item already in a group shows its list.
- The orange accent is replaced by blue (selection, buttons, counts) and yellow (items in the study, logo); the
  figures use gold for the existing design.
- Zoom and pan in the view: the mouse wheel zooms toward the cursor; the bar at the bottom right has Rotate / Pan
  (what a left-drag does; Pan is the default in PLAXIS 2D), + / − and Fit. Right-drag does the other one.

The figures are drawn with matplotlib (200 dpi, captions "Figure n. …" under them) and are the same on the page, in
the Excel workbook (sheets Summary, Runs, Old vs new, Figures) and in the Word report (conclusion, study definition,
existing vs modified design, runs, figures). The PLAXIS connection (automatic, or the port and password typed by
the engineer) is the button in the header. The AI chat and the Solver assistant button are gone.

## New: run optimization from the PLAXIS model (PLAXIS 2D and 3D)

The step bar now runs: **1 Input › 2 PLAXIS model › 3 Variables › 4 Run plan › 5 Compiled scenario › 6 Output ›
7 Results › 8 Compiled results**. Text is shown at double size by default (A− / A+ in the header changes it, and the
choice is remembered) and the colours are green and orange.

- **PLAXIS 2D or PLAXIS 3D** (page 1, two icons; also the badge in the header). It sets the coordinates (2D: x, y
  with y up; 3D: x, y, z with z up), the mesh levels, the result names read from PLAXIS Output and the file type of
  the working copy (.p2dx / .p3d). When the model is read from PLAXIS, PLAXIS itself says which one it is.
- **2 · PLAXIS model: geometry from the PLAXIS file's own coordinates** (no drawing, no CAD). Following the PLAXIS
  Reference Manual (appendix F.2, Python HTTP REST API wrapper), the app connects to PLAXIS Input through the remote
  scripting server and reads: points, lines, soil polygons, boreholes and soil layer levels, plates, anchors,
  geogrids, embedded beams, line / point / surface loads, materials with their parameters, phases and water levels.
  It draws the model (2D section, or a 3D view of the soil block and structures).
  - **Automatic connection, no server set-up by hand.** As soon as the project is created with a PLAXIS file,
    page 2 connects to PLAXIS by itself, whatever the solver setting:
    1. the saved port (saved password, no password, or a password found in PLAXIS' own settings on this PC);
    2. a PLAXIS Input that is already open (found by program name and by its window title): when its scripting
       server is off, the app does in PLAXIS' window what an engineer does by hand (`app/plaxis_gui.py`, with
       `pywinauto`, installed by run.bat): closes the *Plaxis disclaimer*, opens *Expert › Configure remote
       scripting server*, ticks *Show password*, reads the port and the password (types a strong one when none is
       set), presses *Start server* (*Find available* when the port is taken) and *Close*, then connects;
    3. nothing open: it starts PLAXIS Input as a double-click does (the version used last, else the newest
       install), closes the disclaimer and switches the server on through the menu as in 2 (start-up options
       `--AppServerPort` / `--AppServerPassword` are used only when pywinauto is missing, because they crash
       PLAXIS 3D 2025). If a version crashes (PLAXIS' "An error occurred" window is closed by the app) or closes
       by itself, the next installed version is started, and the one that failed is tried last next time.
    Port and password are saved in Settings and calculations switch to real PLAXIS. Your project is never replaced
    by the example model.
  - **Or start the server yourself**: page 2 has a *PLAXIS connection* box. Choose *I press Start server in PLAXIS
    myself*, then in PLAXIS Input use Expert › Configure remote scripting server, tick Show password, press Start
    server, and paste the port and password into the app (*Connect and read*). In this mode the app never starts
    PLAXIS or touches its menus. *Automatic* switches back. A window counts as PLAXIS only when its program is
    PLAXIS Input (a browser tab showing this app is never taken for PLAXIS).
  - **When PLAXIS struggles**: *Stop waiting* while it connects; afterwards *↻ Try again* (connects to the PLAXIS
    that is open now, or keeps waiting for the one the app started), *Restart PLAXIS and connect* (closes only the
    PLAXIS the app started) and *Ask the solver assistant*.
  - **Solver assistant** (orange button, bottom right, every page; offline, no API key): checks this PC
    (plxscripting, PLAXIS Input installs, what is open, its window and ports, the saved port) and says what to do,
    with buttons to Connect, Restart PLAXIS or open Settings. It also explains PLAXIS messages ("soil body seems to
    collapse", no convergence, numerical error), licence, mesh levels, Factor of safety, outputs and the run plan.
  - **Missing files are added on page 2**: *+ PLAXIS project file / zip* and *+ Data folder (.p3dat / .p2dxdat)*
    (a folder upload keeps its sub-folders). When the data folder is missing, the message has an *Upload the
    <name>.p3dat folder* button; after the upload the model is read again by itself.
  - Not on the PC with PLAXIS? *Download export script* gives `plaxis_model.py`. Run it with PLAXIS' Python
    (Expert › Python › Run script, or `python plaxis_model.py --port 10000 --password … --out model.json`) and
    upload the `model.json` it writes.
  - *Load the example model* (only when pressed) loads an example anchored excavation (2D or 3D) to try the workflow.
- **Items to optimize**: every parameter found in the model, grouped (Mesh, each material, Loads, Geometry such as
  the wall toe level, Water conditions, all point coordinates). Tick an item, or click a point in the drawing, and it
  becomes a variable with its PLAXIS reference (e.g. `Clay.phi`, `LineLoad_1_1.qy_start`, `Point_6.y`, `Mesh.size`)
  and its current value; the range starts at ±20 % and can be changed on 3 · Variables.
- **4 · Run plan**: for each criterion (Mesh, Materials, Loads, Geometry, Water, Stages) the number of runs and one
  of two options:
  - **Logical sequencing**: the parameter moves from its start value to its end value in equal parts. With 5 runs
    each run adds 20 % of the change (20, 40, 60, 80, 100 %); with 4 runs 25 %. A list such as the mesh levels
    (Very coarse, Coarse, Medium, Fine, Very fine; PLAXIS values 0.12 … 0.03) is walked evenly from first to last.
  - **Customized sequencing**: a table of runs × parameters where you type every value (it starts from the logical
    values, so only the changes need typing).
  The criteria are combined as *every combination* (Mesh 4 × Materials 5 = 20 runs), *together step by step*, or
  *one criterion at a time*. *Run 0 = the existing model* adds the model as it is, as the reference.
- **5 · Compiled scenario**: every run with the value of each parameter, named like
  `Run 2: LineLoad_1_1 qy -120 kN/m/m / Coarse mesh / Clay Friction angle φ' 28 °`.
- **6 · Output › Outputs to extract at every run**: tick what PLAXIS Output should give for each run (factor of
  safety, total / horizontal / vertical displacement, surface settlement, wall deflection, wall bending moment,
  shear and axial force, anchor force, embedded beam force, excess pore pressure, the run's project file), the phase
  to read it from (the model's phases are listed) and, if needed, a criterion.
- **7 · Results**: every run is shown with its scenario (`Run 2: Load 120 kPa / Coarse mesh / …`), also in the
  CSV and the report; the best design is named the same way.
- **8 · Compiled results**: a summary (range of each output and the runs that gave it, how many meet the criteria,
  the most influential parameter), a table of the effect of each changed parameter, and figures: each output in
  every run, and each output against each changed parameter (one line per combination of the other parameters, or
  every run plus the mean when there are many; the existing model as a grey diamond; the criterion as a red line).
  The same summary and figures are in the printable report.


A web app that follows your UI sketches and the workflow chart:

**Page 1 – Input & objective.** Upload the PLAXIS 3D / PLAXIS 2D project: *+ PLAXIS project file / zip* for the
`.p3d` / `.p2dx` and *+ Data folder (.p3dat / .p2dxdat)* for its data folder (or drop both on the box, or zip them
together). If the folder is missing, a warning offers an *Add the <name>.p3dat folder* button. Add drawings or reports too. Choose one of the four objectives:
Optimize existing design, Automate different combinations, Design change, New design.

**Variables.** One page per PLAXIS Input mode: **Soil** (soil materials with every numeric parameter PLAXIS has for
them, soil layer levels per borehole), **Structures** (geometry points, structure toe levels, plate / anchor /
embedded beam / geogrid materials, loads), **Mesh** (element distribution), **Flow conditions** (groundwater heads)
and **Staged construction** (phase settings: time interval, tolerated error, max steps; and the values that change
per phase: loads, anchor prestress, as `Object.prop@Phase`). Each page lists the parameters read from your PLAXIS
model with their PLAXIS units and current values: tick the ones to vary (no free rows once a model is read).
Sections of older projects (Geometry, Loads, Materials, Water) are moved to the matching mode. Each section has
the same layout as before: the AI assistant (offline or Claude) on the left and that section's variable table on the
right, with a unit, the PLAXIS object.property it changes, and a range (min / max / step) or a list of values.
The assistant puts a new variable under the section it belongs to (e.g. "cohesion" goes to Materials) and says so;
the Section column in the table moves a variable by hand.
- **Geometry › Cross sections**: upload **CAD drawings (DXF / DWG)** or CSV coordinate tables. Every file opens
  inside the app.
  - **CAD only for now (PDF import switched off).** PDF, scanned-PDF, picture and SVG drawings are refused with a
    short reason, because their geometry can't be read reliably:
    - a PDF keeps only loose pen strokes: no layers, so nothing says which line is soil, wall, anchor, water, text,
      hatching or title block, and the app has to guess from line weights and shapes;
    - letters, dimension arrows and hatching are drawn as many tiny lines that look like geometry;
    - there are no real units: the scale (1:100, 1:200 ...) has to be read from the title block or typed in, and one
      wrong scale moves every node;
    - lines that touch on screen are often a hair apart in the file, so soil boxes don't close;
    - a scanned PDF or a photo has no lines at all, only pixels, and has to be traced or read by AI.

    A DXF / DWG keeps the real coordinates in metres and the CAD layers, so soil, structure and water come out right
    and the layer roles are remembered for the next drawing.

    **No CAD software is needed on your PC.**
    - **DXF** is read in pure Python (`ezdxf`, installed by run.bat, with a built-in fallback reader). Ask whoever made
      the drawing (designer, consultant) for a DXF copy: every CAD program saves one.
    - **DWG** (AutoCAD R13 to 2018+) is read by **ezdwg**, an open-source DWG reader installed with pip from
      requirements.txt (run.bat does it), so no CAD or converter is needed either. If ezdwg can't decode a file, the
      app falls back to the free ODA File Converter, AutoCAD, BricsCAD or LibreDWG when one is on the PC.
    - **Any CAD drawing, not one project.** Nothing is tied to a file, an office or layer names; every limit is
      judged against the drawing itself:
      - **Units**: taken from the file ($INSUNITS) when they give sensible section sizes; when they are missing or
        clearly wrong (a millimetre drawing saved as metres), they are worked out from the typical section width
        (metres, millimetres or centimetres) and a note says so. Feet and inches work when the file says so.
      - **Layouts**: one section; many sections side by side or in a grid; sheets in frames with title blocks (a frame
        holding two sections gives two); blocks (exploded), arcs, polylines and site coordinates (real eastings and
        levels).
      - **Big mixed files** (site plan + rows of section sheets + xrefs): lines far from the rest are left out, each
        group of drawings with empty space round it is read at its own size, and drawings much busier than the
        others (plans, layouts) or much flatter (longitudinal profiles, title bars) are skipped, with a note.
      - Tested on 16 kinds of drawing (`test_cad_import_many_kinds_of_drawing`, `test_dwg_many_sections`) and on a real
        11 MB project DWG with ~180 000 objects (about a minute to read, with a progress message).
    - **Still check by hand**: details, legends and tables drawn at a similar size to the sections (rebar details,
      borehole tables) open as windows too; remove them with *Remove*. Section names and chainages are read only when
      they are written as text next to the section (e.g. "SECTION A-A", "CH 1+500"). A section with a single layer
      line inside a plain box can be mistaken for a sheet frame; and the soil / structure / water roles come from
      the layer roles you set (remembered for the next drawing from the same office).
    - **Only a PDF?** Type the section into a CSV table: one row per point, `line,x,level` (level negative downwards),
      rows with the same line name joined in order (names: `ground`, `boundary`, `wall`, `layer`, `anchor`, `water` ...).
      *Download CSV template* in the app (or `samples/cross_section_example.csv`) is a filled-in example.
    The PDF, picture and AI readers are kept in the code: set `PDF_IMPORT=1` in `.env` to switch them back on
    (experimental). Everything below about PDFs and pictures applies only then.
  - **Any project, three tiers of reading** (nothing in the app is tied to one project or one office):
    1. **CAD first, with layer roles.** In *Extracted lines* every CAD layer (or PDF line group) has a role:
       Auto, Soil, Structure, Water or Ignore. A role forces the line type, whatever the automatic rules say.
       Roles chosen on CAD layers are saved in a **layer library** (`data/layer_roles.json`, shared by every
       project), so the next drawing from the same office reads right the first time. Setting a layer back to
       Auto removes it from the library. API: `GET/PUT /api/layer-roles`.
    2. **AI vision for PDFs and scans.** Each reading gets a quality score (Reliable / Check / Poor: size of the
       drawing, closed soil boxes, loose line ends, source, assumed scale). When a PDF reads poorly (for example a
       symbol or title block instead of the section) and the Claude API key is set, **Claude Fable reads the page
       again automatically** and the better reading is kept. Without a key the app says so and keeps the reading.
    3. **Engineer confirmation.** Each section window shows its quality and the reasons, and a *Confirm geometry*
       button with a short checklist (scale, levels, structures, closed boxes) and the checker's name. Any later
       change to the geometry clears the confirmation, and *Review & run* refuses to start until every section is
       confirmed.
  - **Two views**: *2-Dimensional Visualization* (one window per section) and *3-Dimensional Visualization* (the
    whole job as a solid model, in the page or full screen).
  - **Problem type**: a drawing with a retaining wall (ground at different levels on its two sides) is read as a
    retaining wall / excavation; anything else (embankment, footing, slope, piles, tunnel ...) is kept as a
    **General** model: closed soil clusters found from the lines, structures and water as drawn, with its own review.
    The type can be switched by hand. If the lines read from a PDF are not right, *Read the page with Claude Fable*
    or *Read the page as a picture* reads the same page again.
  - **Consultant model** (each section window opens on it): the drawing is read as a whole retaining-wall model:
    which side is excavated, excavation depth and steps, wall type / position / thickness / toe, anchor rows (level,
    angle, free and bonded length, spacing, prestress), struts, soil layers, groundwater behind the wall and in the
    pit, surcharge and model extents. A **consultant review** checks it (wall embedment, model boundaries 3H behind /
    2H in front / below the toe, anchors bonded beyond the active wedge, anchor angles and cover, row spacing,
    dewatering and exit gradient, thin layers, mesh advice) and lists what had to be assumed. **Options**: *As drawn*,
    *Recommended for PLAXIS* (extents and anchors adjusted to the checks, pit dewatered) and *Conservative*
    (water at the surface, 20 kPa). Every value can be edited; the drawing, review and **construction sequence**
    (PLAXIS phases) update as you type. *Use as PLAXIS geometry* writes it into the section's nodes and lines.
  - **3D model**: soil layers as solid volumes, the open pit, diaphragm wall with panel joints and capping beam,
    anchors (tendon, grout body, head, waler) at their spacing, struts, groundwater and surcharge, placed by chainage
    with the Linear or Step transition. **Construction stage** slider and *Play the sequence* dig the pit step by
    step and install the supports. **Walk through**: click, then W A S D and the mouse to walk inside the
    excavation at eye height (Shift runs, Q/E up/down, Space to the pit base). Soil transparency shows the anchors
    behind the wall.
  - **Scanned PDFs and pictures** (PNG, JPG, TIFF ...): with a Claude API key, **Claude Fable** reads the drawing
    like an engineer (dimensions, levels, soil vs structure). Offline, the straight lines are traced from the picture
    so the section still opens; set the scale and add sloped lines (anchors) in the table.
  - Damaged or permission-protected PDFs are repaired on opening; a file with a missing or wrong extension is
    recognised from its content; DXF drawn only in the paper space opens too; very busy drawings are thinned
    (shortest lines left out) instead of refused.
  - **DWG** is read by the built-in reader (ezdwg); only if that fails, with whatever is on the PC: the free ODA File
    Converter, AutoCAD (its console, no window opens), BricsCAD or LibreDWG.
  Vector files are read offline (no API key):
  - **PDF** (exported from AutoCAD, Revit, PLAXIS ...): the drawn lines of every page are read. Each separate drawing
    on a sheet becomes its own cross section; the sheet frame, title block and dimension strings are left out. The
    scale is read from the sheet ("1:200"); if none is found 1:100 is used, and you can change it. Lines are grouped
    by line weight; hatching is switched off and any group can be ticked on or off. A scanned PDF (a picture) has no
    lines to read: that needs the Claude AI, or export the drawing as PDF/DXF from CAD.
  - **DXF** (any version): blocks are exploded, arcs and circles become short straight pieces, and layers can be
    switched on and off (dimension, text, hatch and title layers start switched off). Units come from the drawing.
  - **DWG**: read by the built-in DWG reader (ezdwg, installed with pip), no CAD needed.
  - **CSV/TXT**: one point per row `x, level`, a blank row between lines (a column named `depth` is positive down).
  - **Soil boxes vs structures**: every line is marked *soil* (model boundary, ground surface, layer boundaries,
    excavation, water table) or *structure* (walls, piles, anchors, struts, slabs). It uses the CAD layer names
    (e.g. SOIL, D-WALL, ANCHOR), walls drawn with their thickness, and lines that end in the soil (anchors, the wall
    below the excavation). With a Claude API key the AI checks the result. In the drawing soil lines are thin and
    structures thick; change any line's type in the table, or press *Identify soil / structures again*.
  - **One window per cross section**, each with its drawing and a **live coordinate table**: change a node's x or z,
    add or remove nodes and lines, and the drawing, node labels and horizontal / vertical spacings update as you
    type. Click a node in the drawing to jump to its row. *Open in new window* puts a section in its own browser
    window; edits in either window show in the other straight away.
  - **Big drawings** are read in the background with a progress message, so the page never freezes. PyMuPDF
    (installed by run.bat) reads a large AutoCAD PDF in seconds; without it pdfminer is used, which is slower.
  - **Section toolbar**: a button per section at the top of Geometry opens or closes its window, and *3D view* opens
    the combined model.
  - **Make PLAXIS model geometry** (per section): turns the drawing into a clean model: soil boxes closed, nodes
    where lines cross, small gaps snapped, excavated zones kept as clusters to switch off in the stages, and the
    soil layer depths on each side of the wall listed. Lines are typed *soil*, *structure* or *water* (dashed).
  - **3D view**: one window with all sections placed at their chainage, soil layer surfaces, walls, anchors and
    water levels joined between them. *Transition*: **Linear** (levels change gradually from one section to the
    next) or **Step** (each section holds half way to its neighbours).
  - **Chainage**: read from the drawing ("CH 0+045", "KM 1+200") next to each section. If a section has none, it
    keeps its upload order and is placed evenly between the known sections around it, or beyond them at the usual
    spacing of the known ones. Each one is marked *from the drawing*, *inferred* or *set by you*; type a chainage
    to fix it.
  - **Origin (0, 0, 0) is the upper left; x to the right, z = depth, positive downwards.** y is the section's
    position along the model (0 unless you set it). Download each drawing as SVG. Try the files in `samples/`.
- Mesh variables use the PLAXIS ref `Mesh.size` (relative element size, re-meshes before calculating). Water:
  e.g. `Borehole_1.Head`. Stages: e.g. `Phase_3.TimeInterval`.

**Structures page: the model you can click.** Above the parameter list the model is drawn (3D: 3D, Front x-z,
Side y-z and Top x-y views; *Names* shows the point names). Moving over a point shows its coordinates. Clicking a
point lists its x, y, z: tick a coordinate to vary it (everything drawn on that point moves with it). Clicking a
line, wall, anchor, beam or load lists its points and coordinates, its material and load values, and *Shift in
x / y / z*: the whole object moves by the same amount from where it is now (PLAXIS ref `Point_5.x+; Point_6.x+`).
Picked items are drawn in orange; the full point list stays below (closed) as a fallback.

**Compiled scenario / Compiled results: geometry of each run.** Every run has a *Geometry* button: the model is
drawn as that run changes it (moved points in orange; points move with every structure, load and soil polygon
corner on them; soil layer bottoms and heads move the layers and water level), next to the list of what changed
(existing value, this run's value). *Previous run* / *Next run* step through the runs.

**Page 3 – Output.** Choose the output format:
- **Results**: the target outputs (T1, T2 ...) with their criteria, the goal (Optimize / New design), and the files:
  results table (CSV) and/or the PLAXIS run files (zip).
- **Report**: a printable design report (project, objective, variables by section, cross-section drawings with node
  coordinates, target outputs, selected design, all runs). Open it and use Print › Save as PDF.

**Review & run** shows the task summary (how many PLAXIS runs and what is checked). Then you can **Approve**, **Edit** or **Cancel**.

**Page 4 – Results.** Shows progress, then a table of every run with pass/fail per target, the best design ★,
and the outputs you chose (Report, CSV, run files). **Back to variables** returns to page 2. The step bar under the
header moves between the pages.

What each objective does when it runs:
| Objective | Runs |
|---|---|
| Optimize existing design | the existing model + a grid over the ranges (capped at `MAX_RUNS`), then the best passing design by goal |
| Automate different combinations | every combination of the variable values |
| Design change | the existing model and the changed model, side by side |
| New design | a grid over the ranges, then the best passing design by goal |

Each run is retried up to 3 times (backoff 2, 4, 8 s). If a run still fails, the task shows the error with
**Retry failed runs** / **Edit**, and an email goes to `USER_EMAIL` and `ADMIN_EMAIL`. Every step is written to
the task log (SQLite, shown at the bottom of each page).
Projects made with the earlier version still open; their variables are sorted into sections by name.

## Connecting PLAXIS

Step-by-step guide for Windows: **PLAXIS_SETUP.txt** (and `find_plaxis.bat` to locate plxscripting).

Run the app on the same PC as PLAXIS.

1. In PLAXIS Input, open your project, go to **Expert › Configure remote scripting server**, note the port
   (default 10000) and the password, then press **Start server**.
2. In the app, click **Settings** (top right). Choose *PLAXIS (real calculations)*, enter the same port and
   password, and press **Test connection**. When it works, it lists the materials, structures and phases it found.
3. If the test says it can't find `plxscripting`, paste PLAXIS' Python folder into *plxscripting folder*, usually
   `C:\ProgramData\Bentley\Geotechnical\PLAXIS Python Distribution V2\python\Lib\site-packages`.
4. On page 1, choose **Project open in PLAXIS** (or **File on this PC** and paste the `.p3d` path).

For each run, the app opens a working copy of the model, applies the variables, calculates, reads the targets and
saves the run as its own project under `data/runs/`. Your original file is never changed. The model's
materials, structures and phases are passed to the AI assistant, so it uses the real names in the PLAXIS ref column.

Results it can read: `FoS` (SumMsf of the phase), `Utot_max`, `Ux_max`, `Uy_max`, `Uz_max`, any
`<Group>.<Type>` such as `Plate.M2D` (2D) or `Plate.M11` (3D) (max absolute value at nodes), with an optional
location filter (`Soil.Uy|Y = max` = at the top of the model), or `file`.

**The PLAXIS connection is written against the remote scripting API but has not been tested against a real
PLAXIS installation yet.** Until then, use `SOLVER_BACKEND=simulated`, which produces made-up results so you can
try the whole workflow.

## Run it

**Windows:** double-click `run.bat`, or in Command Prompt from this folder run:

```bat
copy .env.example .env
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000
```

Then open http://127.0.0.1:8000. If `python` isn't found, use `py` instead.

**macOS / Linux:**

```bash
cp .env.example .env
python3 -m pip install -r requirements.txt
python3 -m uvicorn app.main:app --port 8000
```

No API key is needed: the **offline assistant** (`app/offline_ai.py`) understands plain sentences about
parameters, ranges, criteria and goals, and you can edit the tables by hand or upload a CSV. Optionally, add an
Anthropic API key under **Settings** to switch to Claude, which also reads drawings and PDFs. Claude then uses Claude Opus 5.5, reads images and PDFs, and has server-side
refusal fallback enabled. Requires Python 3.10 or newer.

## Tests

```bash
python -m pytest -q
```
