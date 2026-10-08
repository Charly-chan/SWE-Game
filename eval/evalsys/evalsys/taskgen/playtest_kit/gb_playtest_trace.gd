extends Node

                                                                            
                                                                   
const CONFIG := "res://gb_playtest_trace.json"
const LIMIT := 48
const HOLDERS := ["run", "state", "run_state", "game"]
const FIELDS := ["health", "hp", "score", "lives", "ammo", "level", "level_index",
	"stage", "stage_index", "wave", "phase", "dead", "won", "game_over"]

var _fields: Array = FIELDS.duplicate()
var _watches: Array = []
var _targets: Array = []
var _series := {}
var _frame := 0
var _scene := ""
var _actions: Array = []
var _refresh_needed := true
var _operation := {}
var _operation_series := {}
var _last_op_index := -1
var _op_end_tick := 0


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	process_physics_priority = 1000
	get_tree().node_added.connect(func(_node): _refresh_needed = true)
	var config = JSON.parse_string(FileAccess.get_file_as_string(CONFIG))
	if typeof(config) == TYPE_DICTIONARY:
		_watches = config.get("watches", [])
		for field in config.get("fields", []):
			if field not in _fields:
				_fields.append(field)
	for action in InputMap.get_actions():
		if String(action).begins_with("gb_"):
			_actions.append(String(action))
	print("PLAYTEST_TRACE " + JSON.stringify({"kind": "start"}))


func _physics_process(_delta: float) -> void:
	_frame += 1
	_sync_operation()
	var current := get_tree().current_scene
	var scene := current.scene_file_path if current != null else ""
	if scene != _scene or _refresh_needed:
		_scene = scene
		_refresh_targets()
		_refresh_needed = false
	var input_state := {"actions": [], "just_pressed": [], "just_released": []}
	for action in _actions:
		if Input.is_action_pressed(action):
			input_state.actions.append(action)
		if Input.is_action_just_pressed(action):
			input_state.just_pressed.append(action)
		if Input.is_action_just_released(action):
			input_state.just_released.append(action)
	for target in _targets:
		var source = instance_from_id(target.id)
		if not is_instance_valid(source):
			continue
		for field in target.fields:
			_observe(source, target, String(field), input_state)


func _sync_operation() -> void:
	var driver := get_tree().root.get_node_or_null("GBReplayOps")
	if driver == null:
		return
	var tape_tick := int(driver.get("_run_ticks"))
	var op_index := int(driver.get("_op_i"))
	if op_index != _last_op_index:
		_last_op_index = op_index
		_op_end_tick = tape_tick + maxi(int(driver.get("_op_left")), 0)
	var phase := "boot" if tape_tick == 0 else "op"
	if tape_tick > 0 and (op_index < 0 or tape_tick > _op_end_tick):
		phase = "tail"
	var index := op_index if phase == "op" else -1
	if _operation.is_empty() or _operation.phase != phase or _operation.op_index != index:
		_emit_operation()
		_operation = {"kind": "operation", "phase": phase, "op_index": index,
			"first_frame": _frame, "last_frame": _frame,
			"first_tape_frame": tape_tick, "last_tape_frame": tape_tick}
		if phase == "op":
			_operation.expected_last_tape_frame = _op_end_tick
		_operation_series.clear()
	_operation.last_frame = _frame
	_operation.last_tape_frame = tape_tick


func _observe_operation(key: String, target: Dictionary, field: String, value) -> void:
	if _operation.is_empty():
		return
	var prior: Dictionary = _series.get(key, {})
	var has_before: bool = not prior.is_empty() and prior.last_frame == _frame - 1
	var before = prior.last if has_before else null
	if not _operation_series.has(key):
		var numeric = value if typeof(value) in [TYPE_INT, TYPE_FLOAT] else null
		_operation_series[key] = {"scene": _scene, "node": target.node, "script": target.script,
			"property": field, "before_observed": has_before, "before": before,
			"first": value, "last": value, "min": numeric, "max": numeric,
			"changes": 0, "observed_frames": 0, "first_frame": _frame, "last_frame": _frame}
		if numeric != null and has_before and typeof(before) in [TYPE_INT, TYPE_FLOAT]:
			_operation_series[key].min = minf(float(before), float(value))
			_operation_series[key].max = maxf(float(before), float(value))
	var row: Dictionary = _operation_series[key]
	row.last = value
	row.last_frame = _frame
	row.observed_frames += 1
	if has_before and before != value:
		row.changes += 1
	if typeof(value) in [TYPE_INT, TYPE_FLOAT] and row.min != null:
		row.min = minf(float(row.min), float(value))
		row.max = maxf(float(row.max), float(value))


func _emit_operation() -> void:
	if _operation.is_empty():
		return
	_operation.states = _operation_series.values()
	if _operation.phase == "op":
		_operation.complete = _operation.last_tape_frame >= _operation.expected_last_tape_frame
	print("PLAYTEST_TRACE " + JSON.stringify(_operation))


func _refresh_targets() -> void:
	_targets.clear()
	var candidates: Array = get_tree().root.get_children()
	var current := get_tree().current_scene
	if current != null:
		candidates.append(current)
	for node in get_tree().get_nodes_in_group("gb_player"):
		candidates.append(node)
	if not _watches.is_empty():
		var queue: Array = get_tree().root.get_children()
		while not queue.is_empty():
			var node = queue.pop_back()
			candidates.append(node)
			queue.append_array(node.get_children())
	var seen := {}
	for source in candidates:
		if source == self or not is_instance_valid(source):
			continue
		var id: int = source.get_instance_id()
		if seen.has(id):
			continue
		seen[id] = true
		var script = source.get_script()
		if script == null:
			continue
		var path: String = script.resource_path
		if path.get_file().begins_with("gb_playtest_") or source.name == "GBReplayOps":
			continue
		var fields: Array = []
		if source.get_parent() == get_tree().root or source == current or source.is_in_group("gb_player"):
			fields.append_array(_fields)
			for holder in HOLDERS:
				for field in _fields:
					fields.append(holder + "." + String(field))
		for watch in _watches:
			var matches: bool = (watch.has("node") and String(source.get_path()) == String(watch.node))
			matches = matches or (watch.has("script") and path == String(watch.script))
			if matches:
				for field in watch.properties:
					if field not in fields:
						fields.append(field)
		if not fields.is_empty():
			_targets.append({"id": id, "node": String(source.get_path()), "script": path, "fields": fields})


func _read_path(source, field: String):
	var value = source
	for part in field.split("."):
		if typeof(value) == TYPE_OBJECT and is_instance_valid(value):
			value = value.get(part)
		elif typeof(value) == TYPE_DICTIONARY:
			value = value.get(part)
		elif typeof(value) == TYPE_ARRAY and part.is_valid_int() and int(part) >= 0 and int(part) < value.size():
			value = value[int(part)]
		elif typeof(value) in [TYPE_VECTOR2, TYPE_VECTOR2I] and part in ["x", "y"]:
			value = value[0 if part == "x" else 1]
		elif typeof(value) in [TYPE_VECTOR3, TYPE_VECTOR3I] and part in ["x", "y", "z"]:
			value = value[["x", "y", "z"].find(part)]
		else:
			return null
	return value


func _observe(source, target: Dictionary, field: String, input_state: Dictionary) -> void:
	var value = _read_path(source, field)
	if typeof(value) not in [TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING]:
		return
	var key := _scene + ":" + str(target.id) + ":" + field
	_observe_operation(key, target, field, value)
	if not _series.has(key):
		var numeric = value if typeof(value) in [TYPE_INT, TYPE_FLOAT] else null
		_series[key] = {"node": target.node, "script": target.script, "property": field,
			"scene": _scene, "first": value, "last": value, "first_frame": _frame,
			"last_frame": _frame, "changes": 0, "printed": 0, "min": numeric, "max": numeric}
		_emit_change(_series[key], null, value, input_state)
		return
	var row: Dictionary = _series[key]
	row.last_frame = _frame
	if value == row.last:
		return
	var previous = row.last
	row.last = value
	row.changes += 1
	if typeof(value) in [TYPE_INT, TYPE_FLOAT] and typeof(row.min) in [TYPE_INT, TYPE_FLOAT]:
		row.min = minf(float(row.min), float(value))
		row.max = maxf(float(row.max), float(value))
	_emit_change(row, previous, value, input_state)


func _emit_change(row: Dictionary, previous, value, input_state: Dictionary) -> void:
	if row.printed >= LIMIT:
		return
	row.printed += 1
	print("PLAYTEST_TRACE " + JSON.stringify({"kind": "change", "frame": _frame,
		"scene": _scene, "node": row.node, "script": row.script,
		"property": row.property, "before": previous, "after": value,
		"actions": input_state.actions, "just_pressed": input_state.just_pressed,
		"just_released": input_state.just_released}))


func _exit_tree() -> void:
	_emit_operation()
	for row in _series.values():
		row.kind = "summary"
		row.truncated = row.changes + 1 > row.printed
		print("PLAYTEST_TRACE " + JSON.stringify(row))
	print("PLAYTEST_TRACE " + JSON.stringify({"kind": "end", "frames": _frame}))
