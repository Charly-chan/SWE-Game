extends Node

                                                                               
                                                                              
                                                                                
var _steps: Array = []
var _index := 0
var _start_tick := 0
var _entered := false
var _finished := false
var _status := "running"
var _reason := ""
var _history: Array = []
var _tape: Array = []
var _endings := {}
var _target_id := 0
var _tracked_queries := {}
var _seen := {}
var _scene_id := 0
var _scene_name := ""
var _observations: Array = []


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	process_physics_priority = -1000
	var plan = JSON.parse_string(FileAccess.get_file_as_string("res://gb_playtest_plan.json"))
	_steps = plan.steps
	_collect_queries(_steps)
	var manifest = JSON.parse_string(FileAccess.get_file_as_string("res://gb_levels.json"))
	_endings = manifest.get("endings", {})


func _physics_process(_delta: float) -> void:
	if _finished:
		return
	var driver := get_tree().root.get_node_or_null("GBReplayOps")
	if driver == null or driver.get("_phase") != "run":
		return
	var tick := int(driver.get("_run_ticks"))
	var scene := get_tree().current_scene
	var scene_path := scene.scene_file_path if scene != null else ""
	_observe_targets(scene, tick)
	for ending in _endings:
		if _endings[ending] != scene_path:
			continue
		if ending in ["defeat", "failure", "failed", "loss", "lose"]:
			_finish("failed", "entered " + String(ending), tick)
			return
	if int(driver.get("_op_left")) > 0:
		return
	while _index < _steps.size():
		var step: Dictionary = _steps[_index]
		if not _entered:
			_start_tick = tick
			_entered = true
			_target_id = 0
			print("STATE_PLAN_START step=%d tick=%d name=%s" % [_index, tick, step.name])
		var age := tick - _start_tick
		var done: bool = step.has("skip_if") and _predicate(step.skip_if)
		var actions: Array = []
		if done:
			pass
		elif step.kind == "move":
			var actors := _select(step.get("actor", {"group": "gb_player"}))
			if actors.is_empty() or not (actors[0] is Node2D):
				_finish("failed", "movement actor is unreadable", tick)
				return
			var actor: Node2D = actors[0]
			var destination = _destination(step.target, actor)
			if destination == null:
				_finish("failed", "movement target is unreadable", tick)
				return
			var delta: Vector2 = destination - actor.global_position
			done = delta.length() <= float(step.get("distance", 40.0))
			if not done:
				if absf(delta.x) > 2.0:
					actions.append("gb_right" if delta.x > 0 else "gb_left")
				if absf(delta.y) > 2.0:
					actions.append("gb_down" if delta.y > 0 else "gb_up")
		elif step.kind == "input":
			done = _predicate(step.until) if step.has("until") else age >= int(step.frames)
			actions = step.get("actions", [])
		else:
			_finish("failed", "unknown step kind", tick)
			return
		if done and age >= int(step.get("min_frames", 0)):
			_history.append({"step": _index, "name": step.name, "start_tick": _start_tick,
				"end_tick": tick, "status": "reached", "scene": scene_path,
				"observation": _step_observation(step, tick)})
			print("STATE_PLAN_REACHED step=%d tick=%d name=%s" % [_index, tick, step.name])
			_index += 1
			_entered = false
			continue
		if age >= int(step.get("max_frames", 1800)):
			_finish("failed", "step exceeded its input budget", tick)
			return
		var op := {"op": "state", "actions": actions, "frames": 1}
		if step.has("repeat_every"):
			if age % int(step.repeat_every) == 0:
				op.op = "tap"
			else:
				op.actions = []
		var queued: Array = driver.get("_ops")
		queued.append(op)
		_tape.append(op.duplicate(true))
		return
	_finish("complete", "all step targets reached", tick)


func _select(query: Dictionary) -> Array:
	var candidates: Array = []
	if query.has("node"):
		var node = get_tree().current_scene if query.node == "@scene" else get_tree().root.get_node_or_null(query.node)
		if node != null:
			candidates.append(node)
	elif query.has("group"):
		candidates = get_tree().get_nodes_in_group(query.group)
	else:
		var queue: Array = get_tree().root.get_children()
		while not queue.is_empty():
			var node = queue.pop_back()
			candidates.append(node)
			queue.append_array(node.get_children())
	var result: Array = []
	for node in candidates:
		if not is_instance_valid(node) or node.is_queued_for_deletion():
			continue
		if query.has("script"):
			var script = node.get_script()
			if script == null or script.resource_path != query.script:
				continue
		var matches := true
		for key in query.get("where", {}):
			if _read(node, key) != query.where[key]:
				matches = false
		if matches:
			result.append(node)
	return result


func _read(source, path: String):
	var value = source
	for part in path.split("."):
		if typeof(value) == TYPE_OBJECT and is_instance_valid(value):
			value = value.get(part)
		elif typeof(value) == TYPE_DICTIONARY:
			value = value.get(part)
		elif typeof(value) == TYPE_ARRAY and part.is_valid_int() and int(part) >= 0 and int(part) < value.size():
			value = value[int(part)]
		elif typeof(value) in [TYPE_VECTOR2, TYPE_VECTOR2I] and part in ["x", "y"]:
			value = value[0 if part == "x" else 1]
		else:
			return null
	return value


func _destination(query: Dictionary, actor: Node2D):
	var target = instance_from_id(_target_id) if _target_id != 0 else null
	if target == null:
		var nodes := _select(query)
		var nearest := INF
		for node in nodes:
			var point = _read(node, query.get("property", "global_position"))
			if point is Vector2 and actor.global_position.distance_to(point) < nearest:
				target = node
				nearest = actor.global_position.distance_to(point)
		if target == null:
			return null
		_target_id = target.get_instance_id()
	var point = _read(target, query.get("property", "global_position"))
	if not (point is Vector2):
		return null
	var offset: Array = query.get("offset", [0, 0])
	return point + Vector2(float(offset[0]), float(offset[1]))


func _predicate(condition: Dictionary) -> bool:
	if condition.has("all"):
		for item in condition.all:
			if not _predicate(item):
				return false
		return true
	if condition.has("scene"):
		return get_tree().current_scene != null and get_tree().current_scene.scene_file_path == condition.scene
	if condition.has("vanished"):
		var record: Dictionary = _seen.get(JSON.stringify(condition.vanished), {})
		return record.get("first_seen_tick", -1) >= 0 and record.get("last_count", -1) == 0
	var value = _value(condition)
	if value == null:
		return false
	var expected = condition.value
	match condition.get("cmp", "eq"):
		"eq": return value == expected
		"ge": return value >= expected
		"le": return value <= expected
		"gt": return value > expected
		"lt": return value < expected
	return false


func _collect_queries(value) -> void:
	if typeof(value) == TYPE_DICTIONARY:
		if value.has("vanished"):
			_tracked_queries[JSON.stringify(value.vanished)] = value.vanished
		if value.get("kind") == "move" and value.has("target"):
			_tracked_queries[JSON.stringify(value.target)] = value.target
		for child in value.values():
			_collect_queries(child)
	elif typeof(value) == TYPE_ARRAY:
		for child in value:
			_collect_queries(child)


func _observe_targets(scene, tick: int) -> void:
	var id: int = scene.get_instance_id() if scene != null else 0
	if id != _scene_id:
		if not _seen.is_empty():
			_observations.append({"scene": _scene_name, "targets": _seen.values().duplicate(true)})
		_seen.clear()
		_scene_id = id
		_scene_name = scene.scene_file_path if scene != null else ""
	for key in _tracked_queries:
		var count := _select(_tracked_queries[key]).size()
		if not _seen.has(key):
			_seen[key] = {"query": _tracked_queries[key], "first_seen_tick": -1,
				"first_absent_after_seen_tick": -1, "initial_count": count, "last_count": count}
		var record: Dictionary = _seen[key]
		record.last_count = count
		if count > 0 and record.first_seen_tick < 0:
			record.first_seen_tick = tick
		if count == 0 and record.first_seen_tick >= 0 and record.first_absent_after_seen_tick < 0:
			record.first_absent_after_seen_tick = tick


func _value(spec: Dictionary):
	if spec.has("sum"):
		var total := 0.0
		for term in spec.sum:
			var part = _value(term)
			if part == null:
				return null
			total += float(part)
		return total
	if spec.has("count"):
		return _select(spec.count).size()
	var nodes := _select(spec.read)
	return _read(nodes[0], spec.read.property) if not nodes.is_empty() else null


func _condition_observation(condition: Dictionary) -> Dictionary:
	var result := {"satisfied": _predicate(condition)}
	if condition.has("all"):
		result.children = []
		for child in condition.all:
			result.children.append(_condition_observation(child))
	elif condition.has("scene"):
		var scene := get_tree().current_scene
		result.observed = scene != null
		result.actual = scene.scene_file_path if scene != null else null
		result.expected = condition.scene
	elif condition.has("vanished"):
		var record: Dictionary = _seen.get(JSON.stringify(condition.vanished), {})
		result.previously_seen = record.get("first_seen_tick", -1) >= 0
		result.current_count = record.get("last_count")
		result.first_seen_tick = record.get("first_seen_tick", -1)
		result.first_absent_after_seen_tick = record.get("first_absent_after_seen_tick", -1)
	else:
		var value = _value(condition)
		result.observed = value != null
		result.actual = value
		result.expected = condition.value
		result.comparison = condition.get("cmp", "eq")
	return result


func _step_observation(step: Dictionary, tick: int) -> Dictionary:
	var result := {"tape_frame": tick, "elapsed_frames": tick - _start_tick}
	var scene := get_tree().current_scene
	result.scene = scene.scene_file_path if scene != null else null
	if step.has("skip_if"):
		result.skip_if = _condition_observation(step.skip_if)
	if step.kind == "input":
		if step.has("until"):
			result.until = _condition_observation(step.until)
		else:
			result.expected_frames = step.frames
	elif step.kind == "move":
		var actors := _select(step.get("actor", {"group": "gb_player"}))
		result.actor_count = actors.size()
		result.target_count = _select(step.target).size()
		result.target_history = _seen.get(JSON.stringify(step.target), {}).duplicate(true)
		result.distance_limit = float(step.get("distance", 40.0))
		if not actors.is_empty() and actors[0] is Node2D:
			var actor: Node2D = actors[0]
			result.actor_position = [actor.global_position.x, actor.global_position.y]
			var destination = _destination(step.target, actor)
			result.destination_observed = destination != null
			if destination != null:
				result.destination = [destination.x, destination.y]
				result.distance = actor.global_position.distance_to(destination)
	return result


func _finish(status: String, reason: String, tick: int) -> void:
	_status = status
	_reason = reason
	_finished = true
	var result := {"status": status, "reason": reason, "completed_steps": _index,
		"total_steps": _steps.size(), "tape_frames": tick,
		"history": _history, "ops": _tape}
	result.target_observations = _observations + [{"scene": _scene_name, "targets": _seen.values()}]
	if _index < _steps.size():
		result.pending_step = _steps[_index]
		result.pending_frames = tick - _start_tick
		result.pending_observation = _step_observation(_steps[_index], tick)
	var file := FileAccess.open("res://gb_playtest_plan_result.json", FileAccess.WRITE)
	file.store_string(JSON.stringify(result, "  ") + "\n")
	print("STATE_PLAN_RESULT status=%s completed=%d/%d tick=%d reason=%s" % [status, _index, _steps.size(), tick, reason])
