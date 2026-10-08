class_name GBNavRunner
extends RefCounted

                                                                             
                                                                           
                                                                           

const DRIFT_TOLERANCE: float = 18.0
const CALIBRATION_FRAMES: int = 180

var replans: int = 0
var plan_ms: int = 0
var expansions: int = 0
var plan_calls: int = 0
var max_drift: float = 0.0
var executed_ticks: int = 0


func calibrate(owner: Node) -> Dictionary:
	_release_all()
	var player_result: Dictionary = _unique_player(owner)
	if not bool(player_result.get("ok", false)):
		return player_result
	var player: CharacterBody2D = player_result["player"]
	var shape_rects: Array[Rect2] = GBNavWorld.collision_rects(player)
	if shape_rects.is_empty():
		return _malformed("gb_player has no enabled 2D collision shape", {})

	var floor_wait := 0
	while not player.is_on_floor() and floor_wait < CALIBRATION_FRAMES:
		await owner.get_tree().physics_frame
		floor_wait += 1
		if not is_instance_valid(player):
			return _malformed("gb_player disappeared during physics calibration", {})
	if not player.is_on_floor():
		return _malformed("gb_player never reached a measurable floor state", {"frames": floor_wait})

	var ticks_per_second := float(Engine.physics_ticks_per_second)
	var dt := 1.0 / ticks_per_second
	var start_position: Vector2 = player.global_position
	var horizontal_samples: Array[Dictionary] = []
	for action: String in ["gb_right", "gb_left"]:
		GBProbe.press(action)
		for _frame: int in range(10):
			var before_velocity: Vector2 = player.velocity
			var before_position: Vector2 = player.global_position
			await owner.get_tree().physics_frame
			horizontal_samples.append({
				"action": action,
				"velocity": player.velocity,
				"delta_velocity": player.velocity - before_velocity,
				"delta_position": player.global_position - before_position,
			})
		GBProbe.release(action)
		for _frame: int in range(4):
			var before_velocity: Vector2 = player.velocity
			await owner.get_tree().physics_frame
			horizontal_samples.append({
				"action": "release",
				"velocity": player.velocity,
				"delta_velocity": player.velocity - before_velocity,
				"delta_position": Vector2.ZERO,
			})

	var run_speed := 0.0
	var ground_acceleration := 0.0
	var ground_friction := 0.0
	var horizontal_displacement := 0.0
	for sample: Dictionary in horizontal_samples:
		var sample_velocity: Vector2 = sample["velocity"]
		var delta_velocity: Vector2 = sample["delta_velocity"]
		var delta_position: Vector2 = sample["delta_position"]
		run_speed = maxf(run_speed, absf(sample_velocity.x))
		horizontal_displacement += absf(delta_position.x)
		if String(sample["action"]) == "release":
			ground_friction = maxf(ground_friction, absf(delta_velocity.x) / dt)
		else:
			ground_acceleration = maxf(ground_acceleration, absf(delta_velocity.x) / dt)
	if run_speed < 1.0 or horizontal_displacement < 1.0:
		return _malformed("gb_player horizontal response could not be measured", {
			"max_abs_velocity_x": run_speed,
			"sampled_displacement": horizontal_displacement,
			"start": start_position,
			"end": player.global_position,
		})
	if ground_acceleration <= 0.0:
		return _malformed("gb_player ground acceleration could not be measured", {})
	if ground_friction <= 0.0:
		                                                                           
		                                                                          
		return _malformed("gb_player ground friction could not be measured", {})

	while not player.is_on_floor() and floor_wait < CALIBRATION_FRAMES:
		await owner.get_tree().physics_frame
		floor_wait += 1
	if not player.is_on_floor():
		return _malformed("gb_player was not grounded before jump calibration", {})

	var vertical_samples: Array[Dictionary] = []
	var air_acceleration := 0.0
	var air_friction := 0.0
	GBProbe.press("gb_jump")
	await owner.get_tree().physics_frame
	GBProbe.release("gb_jump")
	var became_airborne := not player.is_on_floor()
	var air_frames := 0
	while air_frames < CALIBRATION_FRAMES:
		var before_velocity: Vector2 = player.velocity
		if air_frames == 2:
			GBProbe.press("gb_right")
		elif air_frames == 8:
			GBProbe.release("gb_right")
		await owner.get_tree().physics_frame
		var action := "right" if air_frames >= 2 and air_frames < 8 else "release"
		vertical_samples.append({
			"velocity": player.velocity,
			"delta_velocity": player.velocity - before_velocity,
			"action": action,
		})
		if not player.is_on_floor():
			became_airborne = true
		elif became_airborne and air_frames > 3:
			break
		air_frames += 1
	GBProbe.release("gb_right")
	GBProbe.release("gb_jump")
	if not became_airborne:
		return _malformed("gb_player jump response could not be measured", {
			"minimum_velocity_y": _minimum_velocity_y(vertical_samples),
		})

	var jump_velocity: float = _minimum_velocity_y(vertical_samples)
	var gravity_candidates: Array[float] = []
	var descending_velocities: Array[float] = []
	for sample: Dictionary in vertical_samples:
		var sample_velocity: Vector2 = sample["velocity"]
		var delta_velocity: Vector2 = sample["delta_velocity"]
		if delta_velocity.y > 0.01 and absf(delta_velocity.y) < absf(jump_velocity) * 0.5:
			gravity_candidates.append(delta_velocity.y / dt)
		if sample_velocity.y > 0.0:
			descending_velocities.append(sample_velocity.y)
		if String(sample["action"]) == "right":
			air_acceleration = maxf(air_acceleration, absf(delta_velocity.x) / dt)
		else:
			air_friction = maxf(air_friction, absf(delta_velocity.x) / dt)
	if jump_velocity >= -1.0:
		return _malformed("gb_player jump impulse could not be measured", {
			"minimum_velocity_y": jump_velocity,
		})
	if gravity_candidates.is_empty():
		return _malformed("gb_player gravity could not be measured", {
			"airborne_samples": vertical_samples.size(),
		})
	gravity_candidates.sort()
	var gravity: float = gravity_candidates[gravity_candidates.size() / 2]
	if gravity <= 0.0:
		return _malformed("gb_player gravity measurement was not positive", {"gravity": gravity})
	if air_acceleration <= 0.0:
		return _malformed("gb_player air acceleration could not be measured", {})
	if air_friction <= 0.0:
		return _malformed("gb_player air friction could not be measured", {})

	var terminal_velocity := INF
	var terminal_observed := false
	if descending_velocities.size() >= 4:
		var tail: Array[float] = descending_velocities.slice(descending_velocities.size() - 4)
		var spread: float = tail.max() - tail.min()
		if spread < maxf(1.0, gravity * dt * 0.25):
			terminal_velocity = _average(tail)
			terminal_observed = true

	var physics := {
		"ticks_per_second": ticks_per_second,
		"run_speed": run_speed,
		"ground_acceleration": ground_acceleration,
		"ground_friction": ground_friction,
		"air_acceleration": air_acceleration,
		"air_friction": air_friction,
		"jump_velocity": jump_velocity,
		"gravity": gravity,
		"terminal_velocity": terminal_velocity,
		"floor_snap_length": player.floor_snap_length,
	}
	return {
		"ok": true,
		"player": player,
		"physics": physics,
		"evidence": {
			"run_speed": run_speed,
			"ground_acceleration": ground_acceleration,
			"ground_friction": ground_friction,
			"air_acceleration": air_acceleration,
			"air_friction": air_friction,
			"jump_velocity": jump_velocity,
			"gravity": gravity,
			"terminal_velocity": terminal_velocity if terminal_observed else "no finite cap observed",
			"horizontal_displacement": horizontal_displacement,
			"jump_air_frames": air_frames,
		},
	}


func run_target(
	owner: Node,
	target: Node,
	target_kind: String,
	expected_scene: String,
	physics: Dictionary,
	world_bottom: float,
	budget: int = 1400
) -> Dictionary:
	var target_rects: Array[Rect2] = GBNavWorld.collision_rects(target)
	if target_rects.is_empty():
		return _malformed("%s target has no enabled 2D collision shape" % target_kind, {
			"target": String(target.name),
		})
	var goal_rect: Rect2 = target_rects[0]
	for index: int in range(1, target_rects.size()):
		goal_rect = goal_rect.merge(target_rects[index])

	var remaining := budget
	while remaining > 0:
		var player_result: Dictionary = _unique_player(owner)
		if not bool(player_result.get("ok", false)):
			return player_result
		var player: CharacterBody2D = player_result["player"]
		var mover_velocities: Dictionary = await _measure_movers(owner, player)
		var world := GBNavWorld.capture(owner.get_tree().current_scene, player, world_bottom, mover_velocities)
		if world.player_rect_local.size.x <= 0.0 or world.player_rect_local.size.y <= 0.0:
			return _malformed("gb_player collision bounds could not be captured", {})
		if is_instance_valid(target):
			target_rects = GBNavWorld.collision_rects(target)
			if not target_rects.is_empty():
				goal_rect = target_rects[0]
				for index: int in range(1, target_rects.size()):
					goal_rect = goal_rect.merge(target_rects[index])

		var model := GBNavModel.new()
		model.setup(world, physics, player.global_position, player.velocity, player.is_on_floor())
		var planner := GBPlanner.new()
		var plan: Dictionary = planner.plan(model, goal_rect)
		plan_calls += 1
		plan_ms += int(plan.get("plan_ms", 0))
		expansions += int(plan.get("expansions", 0))
		if not bool(plan.get("ok", false)):
			var failure: Dictionary = _planner_failure(plan)
			failure["geometry"] = _nearby_geometry(world, player.global_position, goal_rect)
			return failure
		var inputs: Array = plan["inputs"]
		var trajectory: Array = plan["trajectory"]
		if inputs.is_empty():
			if _target_succeeded(target, target_kind, expected_scene):
				return await _finish_airborne(owner, target_kind, 0, world_bottom)
			                                                                         
			                                                                     
			                                                                       
			for _confirmation: int in range(8):
				if not is_instance_valid(target):
					return await _finish_airborne(owner, target_kind, 0, world_bottom)
				var live_player: Node = GBProbe.get_player()
				if not live_player is CharacterBody2D:
					return {"ok": false, "verdict": "inconclusive", "error": "gb_player disappeared during target confirmation", "stats": stats()}
				var delta_x: float = goal_rect.get_center().x - (live_player as CharacterBody2D).global_position.x
				_apply_input([1 if delta_x >= 0.0 else -1, 0])
				await owner.get_tree().physics_frame
				executed_ticks += 1
				remaining -= 1
				if _target_succeeded(target, target_kind, expected_scene):
					_release_all()
					return await _finish_airborne(
						owner, target_kind, 1 if delta_x >= 0.0 else -1, world_bottom
					)
			_release_all()
			var current_player: Node = GBProbe.get_player()
			if target is Area2D and current_player is PhysicsBody2D \
					and (target as Area2D).overlaps_body(current_player as PhysicsBody2D):
				return _malformed("engine overlap with grouped target produced no target effect", {
					"target": String(target.name),
					"kind": target_kind,
				})
			return {
				"ok": false,
				"verdict": "inconclusive",
				"error": "planner forecast arrival but live collision geometry did not overlap",
				"stats": stats(),
			}

		var drifted := false
		for index: int in range(inputs.size()):
			_apply_input(inputs[index])
			await owner.get_tree().physics_frame
			executed_ticks += 1
			remaining -= 1
			if _target_succeeded(target, target_kind, expected_scene):
				_release_all()
				return await _finish_airborne(
					owner, target_kind, int((inputs[index] as Array)[0]), world_bottom
				)
			if GBProbe.scene_path() != expected_scene:
				_release_all()
				return {"ok": false, "verdict": "inconclusive", "error": "scene changed before target confirmation", "stats": stats()}
			var live_player: Node = GBProbe.get_player()
			if not live_player is CharacterBody2D:
				_release_all()
				return {"ok": false, "verdict": "inconclusive", "error": "gb_player disappeared during plan execution", "stats": stats()}
			var live_position: Vector2 = (live_player as CharacterBody2D).global_position
			var drift: float = live_position.distance_to(trajectory[index])
			max_drift = maxf(max_drift, drift)
			if live_position.y > world_bottom:
				_release_all()
				return {"ok": false, "verdict": "inconclusive", "error": "live execution fell outside the world; model disagreement", "stats": stats()}
			if drift > DRIFT_TOLERANCE:
				replans += 1
				drifted = true
				break
			if remaining <= 0:
				break
		_release_all()
		if remaining <= 0:
			return {"ok": false, "verdict": "inconclusive", "error": "execution budget exhausted", "stats": stats()}
		if not drifted:
			                                                                        
			                                                                     
			replans += 1
	return {"ok": false, "verdict": "inconclusive", "error": "execution budget exhausted", "stats": stats()}


func stats() -> Dictionary:
	return {
		"search_ms": plan_ms,
		"expansions": expansions,
		"plan_calls": plan_calls,
		"replans": replans,
		"max_drift": max_drift,
		"executed_ticks": executed_ticks,
	}


func _finish_airborne(
	owner: Node,
	target_kind: String,
	direction: int,
	world_bottom: float
) -> Dictionary:
	if target_kind != "collectible":
		return _success()
	var player: Node = GBProbe.get_player()
	if not player is CharacterBody2D or (player as CharacterBody2D).is_on_floor():
		return _success()
	if direction == 0:
		direction = 1 if (player as CharacterBody2D).velocity.x >= 0.0 else -1
	for _frame: int in range(120):
		_apply_input([direction, 0])
		await owner.get_tree().physics_frame
		executed_ticks += 1
		player = GBProbe.get_player()
		if not player is CharacterBody2D:
			_release_all()
			return {"ok": false, "verdict": "inconclusive", "error": "gb_player disappeared while completing an airborne block", "stats": stats()}
		if (player as CharacterBody2D).global_position.y > world_bottom:
			_release_all()
			return {"ok": false, "verdict": "inconclusive", "error": "airborne target recovery fell outside the world", "stats": stats()}
		if (player as CharacterBody2D).is_on_floor():
			_release_all()
			return _success()
	_release_all()
	return {"ok": false, "verdict": "inconclusive", "error": "airborne move block did not return to a floor", "stats": stats()}


func _planner_failure(plan: Dictionary) -> Dictionary:
	var exhausted: bool = bool(plan.get("frontier_exhausted", false))
	return {
		"ok": false,
		"verdict": "unbeatable" if exhausted else "inconclusive",
		"error": "reachable frontier exhausted" if exhausted else "planner search budget exhausted",
		"planner": plan,
		"stats": stats(),
	}


func _nearby_geometry(world: GBNavWorld, start: Vector2, goal: Rect2) -> Dictionary:
	var region := Rect2(start, Vector2.ZERO).merge(goal).grow(80.0)
	var solid_rows: Array = []
	for rect: Rect2 in world.solids:
		if rect.intersects(region, true):
			solid_rows.append(rect)
	var one_way_rows: Array = []
	for rect: Rect2 in world.one_ways:
		if rect.intersects(region, true):
			one_way_rows.append(rect)
	return {
		"start": start,
		"goal": goal,
		"player_rect_local": world.player_rect_local,
		"solids": solid_rows,
		"one_ways": one_way_rows,
	}


func _target_succeeded(target: Variant, target_kind: String, expected_scene: String) -> bool:
	if target_kind == "goal":
		return GBProbe.scene_path() != expected_scene
	if not (target is Object) or not is_instance_valid(target as Object):
		return true
	var node := target as Node
	if not node.is_inside_tree():
		return true
	return node is CanvasItem and not (node as CanvasItem).visible


func _unique_player(owner: Node) -> Dictionary:
	var players: Array[Node] = owner.get_tree().get_nodes_in_group(&"gb_player")
	if players.size() != 1:
		return _malformed("gb_player must exist exactly once", {"count": players.size()})
	if not players[0] is CharacterBody2D:
		return _malformed("gb_player must be a CharacterBody2D for 2D navigation", {
			"actual_type": players[0].get_class(),
		})
	return {"ok": true, "player": players[0] as CharacterBody2D}


func _measure_movers(owner: Node, player: CharacterBody2D) -> Dictionary:
	var before: Dictionary = {}
	_collect_mover_positions(owner.get_tree().current_scene, player, before)
	await owner.get_tree().physics_frame
	var after: Dictionary = {}
	_collect_mover_positions(owner.get_tree().current_scene, player, after)
	var velocities: Dictionary = {}
	var dt := 1.0 / float(Engine.physics_ticks_per_second)
	for key: Variant in before.keys():
		if after.has(key):
			velocities[key] = ((after[key] as Vector2) - (before[key] as Vector2)) / dt
	return velocities


func _collect_mover_positions(node: Node, player: CharacterBody2D, result: Dictionary) -> void:
	if node == player:
		return
	if node is AnimatableBody2D or node is RigidBody2D or node is CharacterBody2D:
		result[node.get_instance_id()] = (node as Node2D).global_position
		return
	for child: Node in node.get_children():
		_collect_mover_positions(child, player, result)


func _apply_input(input: Array) -> void:
	var direction := int(input[0])
	if direction < 0:
		GBProbe.press("gb_left")
		GBProbe.release("gb_right")
	elif direction > 0:
		GBProbe.press("gb_right")
		GBProbe.release("gb_left")
	else:
		GBProbe.release("gb_left")
		GBProbe.release("gb_right")
	if int(input[1]) != 0:
		GBProbe.press("gb_jump")
	else:
		GBProbe.release("gb_jump")


func _release_all() -> void:
	for action: String in ["gb_left", "gb_right", "gb_up", "gb_down", "gb_jump"]:
		GBProbe.release(action)


static func _minimum_velocity_y(samples: Array[Dictionary]) -> float:
	var result := INF
	for sample: Dictionary in samples:
		result = minf(result, (sample["velocity"] as Vector2).y)
	return result


static func _average(values: Array[float]) -> float:
	var total := 0.0
	for value: float in values:
		total += value
	return total / float(values.size()) if not values.is_empty() else 0.0


func _success() -> Dictionary:
	return {"ok": true, "stats": stats()}


func _malformed(message: String, evidence: Dictionary) -> Dictionary:
	_release_all()
	return {
		"ok": false,
		"verdict": "malformed",
		"error": message,
		"evidence": evidence,
		"stats": stats(),
	}
