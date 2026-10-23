"""识图匹配阈值 —— 全项目唯一来源。

集中在这里的理由只有一个：这些数字散落在 40 多处调用点时无法审计，而游戏每次
版本更新都可能让它们集体失效，那时需要一次性重新标定。
**集中是重新标定的前提。**

这些值是实机跑出来的当前最佳值。集中在这里而不是留在各调用点，是因为游戏
每次版本更新都可能让它们集体失效 —— 那时需要一次性重新校准，集中是前提。

改之前先确认这一条是怎么定出来的**（余量小的最容易碎）**：
    * 若你知道它的正/负样本匹配度，把余量写在旁边，例如 `# 余量 0.07`
    * 若不知道，先按 CLAUDE.md §1.2 跑一次探针再改 —— 不要凭手感调
新增常量必须同步补进 `tests/test_thresholds.py` 的 GOLDEN_VALUES。

约定
    * 阈值统一是「严格大于」判定（`max_val > THRESHOLD`），不是「大于等于」。
      改判定方向必须同时改调用点。
    * 命名取自该阈值判定的 UI 元素或图片资源名，尽量与调用点的变量名一致。
    * 同一个数字出现在不同语义下时，**分设不同的常量** —— 它们将来会各自漂移。

不在本模块范围
    HSV 颜色掩膜（`vision/blackscreen.py` 的灰度阈值、`flows/handle.py::take_arrow`
    的蓝色区间、`vision/get_angle.py` 的青色区间）属于另一类识别参数，本次未搬。
"""

# ---------------------------------------------------------------------------
# 通用默认值（Img 的 API 默认参数）
# ---------------------------------------------------------------------------

#: `Img.have_screenshot` / `Img.on_interface` 的默认阈值
DEFAULT_MATCH = 0.90

# Coordinate policy, game units / seconds rather than image probabilities.
# 2026-10-11 traces: 20 Hz, live copies differ <0.00003 units, peak walking
# speed <5.2 units/s. Arrival radius covers one 4 units/s sample plus 50%.
# The larger speed bound rejects discontinuities; timeout/start limits are
# conservative operating budgets, not measurements of game response latency.
COORD_SAMPLE_SECONDS = .05
COORD_CONSENSUS = .01
COORD_REACH_RADIUS = .3
# 2026-10-20 failed oak run: actor stood 0.95 units from the solid center.
# A separate conservative interaction envelope avoids demanding entry into it;
# ordinary saved-waypoint precision remains 0.3. Live drop effect is rechecked.
COORD_SPECIAL_INTERACTION_RADIUS = 1.25
# The outer valid ring can lie 1.2 units from the center. Its final approach
# needs finer steering than normal 0.3-unit waypoints to enter the 1.25 envelope.
COORD_SPECIAL_APPROACH_PRECISION = .05
# A press is only worth judging once the read-only sample can reflect it. The
# scene worker normally repolls every 2 s; a forced refresh still costs a game
# frame plus that poll, so the settle window covers one refreshed sample and a
# second one before the press is called ineffective.
COORD_SPECIAL_STATE_SETTLE_SECONDS = 1.8
COORD_SPECIAL_STATE_POLL_SECONDS = .35
# Presses that change nothing are guesses, not gathering: stop and ask the
# player to check the bag instead of hammering the interaction key.
COORD_SPECIAL_UNCHANGED_LIMIT = 3
COORD_WAYPOINT_SPACING = .4
COORD_IDLE_RADIUS = .02
COORD_DWELL_SECONDS = .3
COORD_MAX_SPEED = 20.0
# Playback frame-window quantization allowance, in game units. Operational
# policy; preserves the old .05 allowance while retaining a full sample window.
COORD_POSITION_SLACK = .05
COORD_MAX_START_DISTANCE = 1.5
COORD_MAX_HEIGHT_ERROR = 1.0
COORD_CALIBRATION_SECONDS = .35
COORD_SETTLE_SECONDS = .15
COORD_MIN_CALIBRATION = .15
COORD_MAX_CALIBRATION_DISTANCE = 2.5
COORD_CALIBRATION_COSINE = .65
COORD_MAX_DIRECTION_CHANGE_DEGREES = 50.0
COORD_STUCK_SECONDS = 2.0
COORD_STUCK_DISTANCE = .15
COORD_MAX_DURATION = 3600.0
COORD_MAX_SAMPLES = 72001
# Additional operating policies: reverse motion must oppose calibration,
# tolerate animation settling by 60% (at least .4 units); playback gets
# four times the recorded duration plus a minute for calibration/correction.
COORD_MIN_RECORDING_MOVEMENT = .5
COORD_REVERSE_COSINE = -.5
COORD_RETURN_ERROR = .4
COORD_RETURN_FRACTION = .6
COORD_PLAYBACK_TIME_FACTOR = 4.
COORD_PLAYBACK_EXTRA_SECONDS = 60.

# Monitor display budgets: 10 Hz is half the verified recorder sampling rate;
# retry at most every 2 s, mark a stalled browser feed after 1.5 s. A bounded
# 1,800 point trail avoids growing browser memory during long sessions.
COORD_MONITOR_REFRESH_SECONDS = .1
COORD_MONITOR_RETRY_SECONDS = 2.
COORD_MONITOR_STALE_SECONDS = 1.5
COORD_MONITOR_TRAIL_POINTS = 1800
# Manual point playback policy: at most ten minutes of active movement per run.
# This is an operational time limit, not a measured game movement threshold.
COORD_POINT_PLAYBACK_SECONDS = 600.
# Finite replay policy (2026-10-12): editable 1-999 cycles, default one.
# This is an operational/UI cap, not a measured game threshold. The ten-minute
# movement/recovery budget above applies separately to each selected cycle.
COORD_PLAYBACK_DEFAULT_LOOPS = 1
COORD_PLAYBACK_MAX_LOOPS = 999
# Named point route policy: cap display/file names at the existing 80-character
# route title budget; this is a UI limit, not a measured game threshold.
COORD_POINT_ROUTE_NAME_LENGTH = 80

# Local recovery operating limits, not measured obstacle dimensions. Chosen
# for the initial bounded detour: .6 units back, 1.2 sideways, 1.8 ahead;
# at the simulated 3 units/s each leg fits 1.25 s. Live traces peaked below
# 5.2 units/s; the 4-unit envelope and per-leg deadline bound failed probes.
# User policy (2026-10-12): five consecutive failures, jump enabled, long Space before
# lateral probes on every attempt, including retries at the same obstacle.
# The 1 s hold and .5 s observation are initial operating
# budgets, not measured game animation durations. Stop forward input after
# 1.8 units, keeping the existing 4-unit envelope. Live jump response still
# needs game validation; the hold remains interruptible by F6/F9/focus loss.
COORD_RECOVERY_DEFAULT_ATTEMPTS = 5
COORD_RECOVERY_MAX_ATTEMPTS = 10
COORD_RECOVERY_BACK_DISTANCE = .6
COORD_RECOVERY_SIDE_DISTANCE = 1.2
COORD_RECOVERY_FORWARD_DISTANCE = 1.8
COORD_RECOVERY_MAX_RADIUS = 4.
COORD_RECOVERY_LEG_SECONDS = 1.25
COORD_RECOVERY_JUMP_SECONDS = 1.
COORD_RECOVERY_JUMP_SETTLE_SECONDS = .5
# Adaptive recovery policy (2026-10-12), verified in simulated barriers, not
# measured game geometry: require 1 unit of NET progress toward the target to
# clear a failure episode (.15-unit animation/jitter is insufficient). Widen
# lateral probes after each left/right pair, up to 2.4 units; all planned legs
# remain inside the existing 4-unit radius/corridor and per-leg time budget.
COORD_RECOVERY_CONFIRM_DISTANCE = 1.
COORD_RECOVERY_SIDE_STEP = .6
COORD_RECOVERY_MAX_SIDE_DISTANCE = 2.4
# User policy (2026-10-13): keep retrying after a failed round. A short release
# interval avoids input thrashing and stays interruptible; operational budget.
COORD_RECOVERY_RETRY_SECONDS = .5

# Point collection policy: one .12 s F pulse, then a user-chosen dwell.
# 3 s is an editable default, not a measured collection-animation duration.
# Cap each wait at five minutes to keep configuration and run budgets bounded.
COORD_COLLECTION_PRESS_SECONDS = .12
COORD_COLLECTION_DEFAULT_WAIT = 3.
COORD_COLLECTION_MAX_WAIT = 300.


# ---------------------------------------------------------------------------
# 主界面与战斗判定
# ---------------------------------------------------------------------------

#: 左上角灯泡：判断「在主界面」
MAIN_INTERFACE = 0.90

#: 主界面的严格判定：`fight_elapsed` 用它判断战斗是否结束
MAIN_INTERFACE_STRICT = 0.92

#: 疑问气泡（`doubt.png`）：附近有敌人待触发
DOUBT_ICON = 0.92

#: `round.png`：必定不在战斗中
ROUND_ICON = 0.95

#: `battle_esc_check.png`：战斗中按 ESC 后的确认画面
BATTLE_ESC_CHECK = 0.97

# 2026-10-02 雅努斯密径实机 1920x1080 样本，TM_CCOEFF_NORMED。
# 正样本均为 1.000；同一弹窗去除对应文字/按钮的负样本分别为 0.243 / 0.447；
# 解谜画面负样本为 0.147 / 0.265。实机样本见 tests/fixtures/puzzle_exit/dialog.png。
PUZZLE_EXIT_PROMPT = 0.90
PUZZLE_EXIT_CONFIRM = 0.90

#: `not_auto.png` / `not_auto_c.png`：自动战斗未开启
AUTO_OFF_ICON = 0.95

#: 行动条识别（判定自动战斗卡住），用实时截图与首帧比较
ACTION_BAR = 0.97

#: `switch_run.png`：疾跑图标。判定「是否在疾跑」要求极高匹配度
SPRINT_ICON = 0.996


# ---------------------------------------------------------------------------
# F 键交互
# ---------------------------------------------------------------------------

#: 扫描 `F` 交互图标（`sw.png` 等一组图）
F_ICON = 0.95


# ---------------------------------------------------------------------------
# 地图与传送
# ---------------------------------------------------------------------------

#: `map_load.png`：地图加载中
MAP_LOADING = 0.95

#: `picture\orientation_1.png`：星轨航图的星球入口图标
ORIENTATION_ICON = 0.97

#: `contraction.png`：地图已打开
MAP_OPEN = 0.97

#: `map_back.png`：地图返回按钮
MAP_BACK = 0.99

#: `find_transfer_point` 的起始搜索阈值
TRANSFER_POINT_SEARCH = 0.99

#: `find_transfer_point` 阈值逐轮下降的下限
TRANSFER_POINT_MIN = 0.93

#: `find_scene` 的起始搜索阈值
SCENE_SEARCH = 0.99

#: `find_scene` 阈值逐轮下降的下限
SCENE_MIN = 0.93

#: `1floor.png` / `2floor.png` / `3floor.png`：楼层按钮
FLOOR_BUTTON = 0.93

#: `orientation_*.png`：星球的定位与查找
PLANET = 0.975

#: 点击星球
PLANET_CLICK = 0.93

#: 通用地图点位查找（`find_transfer_point` 的 threshold 实参）
POINT_SEARCH = 0.975

#: 点击普通传送点
TELEPORT_POINT = 0.93

#: `kaituoli_1.png`：星轨航图
STAR_MAP = 0.97

#: 右上角返回按钮
BACK_BUTTON = 0.94

#: `transfer.png`：地图内传送图标
TRANSFER_ICON = 0.93

#: `max.png`：购买入口图标
BUY_ICON = 0.93

#: `check_4-1_point*.png`：筑梦机关检查
DREAM_MACHINE = 0.992

#: `map_4-1_point_2.png` / `map_4-3_point*.png`：筑梦边境系列点位
DREAM_POINT = 0.975

#: 筑梦边境点位的点击（阈值低于查找）
DREAM_POINT_CLICK = 0.95

#: 筑梦边境场景的拖动查找
DREAM_SCENE = 0.990

#: `finish_fighting.png`：筑梦模块加载完成
DREAM_BUILD = 0.90


# ---------------------------------------------------------------------------
# 设置菜单（自动使用秘技消耗品）
# ---------------------------------------------------------------------------

#: `setting_icon.png`
SETTING_ICON = 0.98

#: `setting_other.png`
SETTING_OPTION = 0.98

#: `auto_use_technique_consumable.png` 开关
TECHNIQUE_CONSUMABLE = 0.98

#: `setting_yes.png` 确认按钮
SETTING_CONFIRM = 0.99


# ---------------------------------------------------------------------------
# 秘技点与道具
# ---------------------------------------------------------------------------

#: `eat.png`：秘技点不足对话框
TECHNIQUE_DIALOG = 0.90

#: `qiqiao.png`：奇巧零食图标
QIQIAO_ICON = 0.95

#: 零食店里的合成/确认按钮。
#: 注意：与 ROUND_ICON 是同一张 round.png，但用途与阈值都不同。
SNACK_CRAFT_BUTTON = 0.9

#: `qiqiao_lab.png`：奇巧零食合成页
QIQIAO_LAB = 0.97

#: `finish_fighting*.png`：判定「已脱离黑屏」
FINISH_FIGHTING = 0.9

#: `round_disable.png`：无法购买
ROUND_DISABLE = 0.95

#: `cancel.png`：取消按钮
CANCEL_BUTTON = 0.95


# ---------------------------------------------------------------------------
# 战斗结算
# ---------------------------------------------------------------------------

#: `continue_fighting.png`
CONTINUE_FIGHTING = 0.98

#: `defeat.png`：战败
DEFEAT = 0.98


# ---------------------------------------------------------------------------
# 月卡
# ---------------------------------------------------------------------------

#: `monthly_pass_pic*.png`：月卡入口
MONTHLY_PASS = 0.91

#: `finish_fighting.png`：判定为「无月卡」
MONTHLY_PASS_NO_PASS = 0.92


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

#: 低于此匹配度才记入 `img_search_val_dict`，供报告输出「最相似图片」
LOW_MATCH_REPORT_FLOOR = 0.99

# Mono metadata traversal budgets. Read-only probe on 2026-10-12 observed 68
# loaded assemblies, <=6,098 cached classes/image, <=504-byte player objects.
# These are corruption/termination bounds, not movement or detection tolerances.
MONO_ASSEMBLY_LIMIT = 512
MONO_CLASS_LIMIT = 100000
MONO_PARENT_LIMIT = 16
MONO_FIELD_LIMIT = 512
MONO_VTABLE_LIMIT = 4096
MONO_OBJECT_LIMIT = 65536
MONO_NAME_BYTES = 256
# Read-only scene-map operating budgets (2026-10-13). The live registry probe
# contained about 7400 hierarchies and 785 colliders. These caps bound parsing
# and refresh work; they are not a claim that the whole world is loaded.
COORD_SCENE_MAX_ITEMS = 20000
# Container capacity includes reserved/removed slots; only live entries count
# toward MAX_ITEMS. Reads still obey COORD_SCENE_MAX_BYTES independently.
COORD_SCENE_MAX_CAPACITY = 262144
# Local navigation starts with conservative padding; these are initial settings,
# verified in simulations, not measured character capsule dimensions.
COORD_NAV_GRID_STEP = .75
COORD_NAV_CLEARANCE = .6
COORD_NAV_WATER_CLEARANCE = 1.
COORD_NAV_GROUND_CLEARANCE = .25
COORD_NAV_BODY_HEIGHT = 1.75
COORD_NAV_HEIGHT_REFRESH = .75
COORD_NAV_SEARCH_RADIUS = 32.
COORD_NAV_HORIZON = 24.
COORD_NAV_MAX_NODES = 7500
COORD_NAV_LEG_LENGTH = 3.
COORD_NAV_MIN_PROGRESS = 2.
COORD_NAV_PROBE_SECONDS = 20.
COORD_NAV_RETRY_SECONDS = .5
COORD_NAV_SCENE_MAX_AGE = 6.
# Manual collection points can be recorded close to a trunk. Only their final
# approach may use the observed endpoint's gap; solid geometry and water stay blocked.
COORD_NAV_APPROACH_RADIUS = 1.5
COORD_NAV_APPROACH_MIN_GAP = .05
COORD_NAV_LEG_REACH_RADIUS = .6
COORD_NAV_RECOVERY_WAIT_SECONDS = 3.
COORD_NAV_APPROACH_SETTLE_SECONDS = .08
# Keep a stable aim along a checked corridor instead of chasing dense samples.
COORD_NAV_STEERING_LOOKAHEAD = 4.
COORD_NAV_MEMORY_LOOKAHEAD = 3.
COORD_NAV_MEMORY_CHORD_ERROR = .15
# Fallbacks are only used when the normal local search finds no route.
COORD_NAV_FINE_GRID_STEP = .375
COORD_NAV_FINE_SEARCH_RADIUS = 16.
COORD_NAV_WIDE_GRID_STEP = 1.
COORD_NAV_WIDE_SEARCH_RADIUS = 60.
COORD_NAV_WIDE_HORIZON = 48.
COORD_NAV_WIDE_MAX_NODES = 16000
COORD_SCENE_MAX_BYTES = 4194304
COORD_SCENE_REFRESH_SECONDS = 2.
# Display-only expansion requested on 2026-10-15. Navigation still consumes
# fresh snapshots; historical fixed geometry never becomes an obstacle source.
COORD_SCENE_RADIUS = 150.
# Display-only horizontal detail radius, requested by the user; not a measured
# game loading distance, collision tolerance or conversion to physical metres.
COORD_MAP_DETAIL_RADIUS = 20.
COORD_SCENE_LIVE_RADIUS = 80.
COORD_SCENE_CACHE_CELL_SIZE = 64.
COORD_SCENE_CACHE_FAR_SECONDS = 10.
COORD_SCENE_CACHE_TTL = 1800.
COORD_SCENE_CACHE_MAX_ITEMS = 20000
COORD_SCENE_CACHE_MAX_CELLS = 256
COORD_TOOL_REFRESH_SECONDS = 1.
COORD_TOOL_LOW_PERCENT = 20.
COORD_SCENE_ENTITY_COMPONENT_LIMIT = 128
COORD_SCENE_LABEL_MAX_CHARS = 96
COORD_SCENE_MIN_QUATERNION_NORM = .5
COORD_SCENE_MAX_QUATERNION_NORM = 1.5

# Legacy memories have no timing: use a conservative walking-speed estimate
# only for ranking, never for movement control or arrival decisions.
COORD_MEMORY_ESTIMATED_SPEED = 3.
# Repeated blocked corridors remain available without unbounded score growth.
COORD_MEMORY_BLOCKED_PENALTY_CAP = 10

# Thin projection of a verified vertical face; navigation retains its body clearance.
COORD_TERRAIN_EDGE_HALF_WIDTH = .02

# Current backpack X scores 0.899-0.918 on real 1024x768 captures; the old .92
# rejected it. Header-relative position and two stable frames remain mandatory.
COORD_FOOD_CLOSE_MATCH = .82
