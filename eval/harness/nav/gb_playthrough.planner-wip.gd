extends Node

const GBNavRunnerScript: Script = preload("res://nav/gb_nav_runner.gd")

                                                                               
                                                                                 
                                                                                
                                                                               
                                                                          
                                               
const PLANNER_ENV: String = "GB_NAV_PLANNER"

                                                                    
 
                                                                            
                                                                  
 
                                             
                                                                       
                                                                                
                                                                                          
                                                                              
                                                                               
                                                     
                                                                               
                                                                             
 
                                                                             
                                                                                 
                                                                                
                                                                          
                                                        

const MAX_TOTAL_FRAMES: int = 5400                      
const MAX_LEVEL_FRAMES: int = 2400
const COLLECT_BUDGET_FRAMES: int = 1200                                                    

const ARRIVE_X_TOL: float = 10.0
const JUMP_UP_THRESHOLD: float = 20.0
const STUCK_FRAMES: int = 45
const MAX_STUCK_JUMPS: int = 3
const JUMP_COOLDOWN_FRAMES: int = 12

const LOOKAHEAD_NEAR: float = 26.0
                                                                             
                                                                                
                                                                                
                                                                     
                                               
const LOOKAHEAD_FAR: float = 210.0
const LOOKAHEAD_STEP: float = 12.0
const LANDING_INSET: float = 14.0
const AIR_X_TOL: float = 2.0
const MAX_COMMIT_FRAMES: int = 120
                                                                              
                                                                            
                                                                        
                                                               
const REACH_X: float = 90.0
const GROUND_RAY_LENGTH: float = 110.0
const FALL_MARGIN: float = 400.0

                                                                              
                                                                                
                                                                               
                                                                    
const ABANDON_COOLDOWN_FRAMES: int = 150
const MAX_ABANDONS_PER_TARGET: int = 4

var _visited: Array[String] = []
var _collected_by_level: Dictionary = {}
var _cooldown_until: Dictionary = {}
var _abandon_count: Dictionary = {}
var _total_frames: int = 0
var _jump_cooldown: int = 0
var _held_direction: String = ""
                                                                             
                                                                                 
var _trace: Array[String] = []
                                                                                
                                                                               
                                                                           
var _air_goal_x: float = 0.0
var _air_frames: int = 0
var _air_committed: bool = false
var _tracing: bool = false
var _last_cliff: String = "-"
var _last_safe_x: float = 0.0
var _navigation_by_level: Dictionary = {}
var _physics_by_level: Dictionary = {}


                                                                            

func run(tracing: bool = false) -> Dictionary:
	_reset_state(tracing)
	await get_tree().process_frame

	var levels: Array = _read_levels()
	if levels.is_empty():
	return _result("malformed", "normalized interface has no level address", {})

	if GBProbe.scene_path() != String(levels[0]):
		var change_error: Error = get_tree().change_scene_to_file(String(levels[0]))
		if change_error != OK:
			return _result("malformed", "could not load the first level", {"change_error": change_error})
	if not await _settle_scene():
		return _result("inconclusive", "first level never settled", {})

	for level_index: int in range(levels.size()):
		var expected: String = String(levels[level_index])
		if GBProbe.scene_path() != expected:
			return _result("inconclusive", "unexpected scene at level index %d" % level_index, {})
		_visited.append(expected)
		_cooldown_until.clear()
		_abandon_count.clear()

		var is_final: bool = level_index == levels.size() - 1
		var result: Dictionary = await (
			_play_level_with_planner(expected, is_final) if _planner_enabled()
			else _play_level(expected, is_final)
		)
		if not bool(result.get("ok", false)):
			return _result(
				String(result.get("verdict", "inconclusive")),
				String(result.get("error", "level failed")),
				result
			)
		if is_final:
			break
		if not await _settle_scene():
			return _result("inconclusive", "next level never settled", result)

	return _result("completed", "completed all levels and cleared the final level's collectibles", {})


func _reset_state(tracing: bool) -> void:
	_visited.clear()
	_collected_by_level.clear()
	_cooldown_until.clear()
	_abandon_count.clear()
	_total_frames = 0
	_jump_cooldown = 0
	_held_direction = ""
	_trace.clear()
	_air_goal_x = 0.0
	_air_frames = 0
	_air_committed = false
	_tracing = tracing
	_last_cliff = "-"
	_last_safe_x = 0.0
	_navigation_by_level.clear()
	_physics_by_level.clear()


                                                                              
                                                                               
                                                                               
                                            
func _planner_enabled() -> bool:
	return OS.get_environment(PLANNER_ENV) == "1"


func _play_level_with_planner(expected_scene: String, is_final: bool) -> Dictionary:
	var frame_start: int = Engine.get_physics_frames()
	var initial_count: int = _visible_collectible_count()
	var goals: Array[Node] = _live_group_nodes("gb_goal")
	if not is_final and goals.is_empty():
		return _planner_level_failure(
			"malformed", "non-final level has no navigable gb_goal", frame_start, {}
		)
	if is_final and initial_count == 0:
		return _planner_level_failure(
			"malformed", "final level has no navigable gb_collectible target", frame_start, {}
		)

	var navigation = GBNavRunnerScript.new()
	var calibration: Dictionary = await navigation.calibrate(self)
	if not bool(calibration.get("ok", false)):
		return _planner_level_failure(
			String(calibration.get("verdict", "malformed")),
			String(calibration.get("error", "physics calibration failed")),
			frame_start,
			calibration
		)
	var physics: Dictionary = calibration["physics"]
	_physics_by_level[expected_scene] = calibration.get("evidence", {})
	if GBProbe.scene_path() != expected_scene:
		return _planner_level_failure(
			"inconclusive", "scene changed during physics calibration", frame_start, calibration
		)

	                                                                           
	                                                                            
	                                       
	while _visible_collectible_count() > 0:
		var target: Node = _nearest_node(_visible_collectibles(), _player_position())
		if target == null:
			return _planner_level_failure(
				"malformed", "collectible group contains no live navigable node", frame_start, {}
			)
		var collect_result: Dictionary = await navigation.run_target(
			self,
			target,
			"collectible",
			expected_scene,
			physics,
			_world_bottom() + FALL_MARGIN
		)
		if not bool(collect_result.get("ok", false)):
			_navigation_by_level[expected_scene] = navigation.stats()
			return _planner_level_failure(
				String(collect_result.get("verdict", "inconclusive")),
				String(collect_result.get("error", "planner could not reach collectible")),
				frame_start,
				collect_result
			)
		if Engine.get_physics_frames() - frame_start > MAX_LEVEL_FRAMES:
			_navigation_by_level[expected_scene] = navigation.stats()
			return _planner_level_failure(
				"inconclusive", "planner execution exceeded the level budget", frame_start,
				{"stats": navigation.stats()}
			)

	_collected_by_level[expected_scene] = initial_count
	if is_final:
		_navigation_by_level[expected_scene] = navigation.stats()
		var used: int = Engine.get_physics_frames() - frame_start
		_total_frames += used
		return {"ok": true, "frames": used, "navigation": navigation.stats()}

	goals = _live_group_nodes("gb_goal")
	var goal: Node = _nearest_node(goals, _player_position())
	if goal == null:
		return _planner_level_failure(
			"malformed", "gb_goal disappeared before it could be navigated", frame_start, {}
		)
	var goal_result: Dictionary = await navigation.run_target(
		self,
		goal,
		"goal",
		expected_scene,
		physics,
		_world_bottom() + FALL_MARGIN
	)
	_navigation_by_level[expected_scene] = navigation.stats()
	if not bool(goal_result.get("ok", false)):
		return _planner_level_failure(
			String(goal_result.get("verdict", "inconclusive")),
			String(goal_result.get("error", "planner could not reach goal")),
			frame_start,
			goal_result
		)
	var used: int = Engine.get_physics_frames() - frame_start
	_total_frames += used
	return {"ok": true, "frames": used, "navigation": navigation.stats()}


func _planner_level_failure(
	verdict: String,
	message: String,
	frame_start: int,
	detail: Dictionary
) -> Dictionary:
	var used: int = Engine.get_physics_frames() - frame_start
	_total_frames += used
	var failure: Dictionary = _failure(verdict, message, used)
	failure["detail"] = detail
	return failure


func _live_group_nodes(group_name: String) -> Array[Node]:
	var result: Array[Node] = []
	for value: Variant in GBProbe.group_nodes(group_name):
		if not value is Node or not is_instance_valid(value):
			continue
		var node: Node = value
		if not node.is_inside_tree():
			continue
		if node is CanvasItem and not (node as CanvasItem).visible:
			continue
		result.append(node)
	return result


func _nearest_node(nodes: Array[Node], position: Vector2) -> Node:
	var nearest: Node = null
	var nearest_distance := INF
	for node: Node in nodes:
		if not node is Node2D:
			continue
		var distance: float = position.distance_squared_to((node as Node2D).global_position)
		if distance < nearest_distance:
			nearest_distance = distance
			nearest = node
	return nearest


func _play_level(expected_scene: String, is_final: bool) -> Dictionary:
	var initial_count: int = _visible_collectible_count()
	var frames_here: int = 0
	var target: Node = null
	var stuck_frames: int = 0
	var stuck_jumps: int = 0
	var previous_x: float = _player_position().x
	var target_landings: int = 0
	var was_on_floor: bool = true
	var bounds_bottom: float = _world_bottom()

	while frames_here < MAX_LEVEL_FRAMES and _total_frames < MAX_TOTAL_FRAMES:
		await get_tree().physics_frame
		frames_here += 1
		_total_frames += 1
		if _jump_cooldown > 0:
			_jump_cooldown -= 1
		                                                                    
		if GBProbe.scene_path() != expected_scene:
			_release_motion()
			_held_direction = ""
			_collected_by_level[expected_scene] = initial_count - _visible_collectible_count()
			if is_final:
				return _failure("unbeatable", "final level exited unexpectedly", frames_here)
			return {"ok": true, "frames": frames_here}

		var position: Vector2 = _player_position()

		                                                                       
		if position.y > bounds_bottom + FALL_MARGIN:
			_release_motion()
			return _failure("unbeatable", "player fell out of the world", frames_here)

		var on_floor: bool = _player_on_floor()
		if on_floor:
			_last_safe_x = position.x
			                                                                     
			                                                                   
			                                                                
			                   
			if _jump_cooldown == 0:
				_air_committed = false
				_air_frames = 0
		elif _air_committed:
			_air_frames += 1
			if _air_frames > MAX_COMMIT_FRAMES:
				                                                           
				                                                       
				_air_committed = false

		                                                                     
		                                                                  
		                                                                   
		                                                                     
		                                                                       
		                                                
		if on_floor and not was_on_floor and _target_is_valid(target):
			                                                                   
			                                                                     
			                                                            
			target_landings += 1
			if target_landings > 3:
				_abandon(target)
				target = null
		was_on_floor = on_floor

		                                                                      
		                                                                    
		                     
		if on_floor and not is_final and frames_here >= COLLECT_BUDGET_FRAMES \
				and _target_is_valid(target) and (target as Node).is_in_group("gb_collectible"):
			target = null

		if on_floor and not _target_is_valid(target):
			var picked: Dictionary = _pick_target(is_final, frames_here, position)
			target = picked.get("node") as Node
			stuck_frames = 0
			stuck_jumps = 0
			target_landings = 0
			if target == null:
				_release_motion()
				_held_direction = ""
				if is_final and _visible_collectible_count() == 0:
					_collected_by_level[expected_scene] = initial_count
					return {"ok": true, "frames": frames_here}
				                                                               
				                                        
				if _has_benched_target():
					continue
				return _failure(
					"unbeatable",
					"no reachable target left" if is_final else "no reachable goal",
					frames_here
				)

		if not _target_is_valid(target):
			                                                                
			                                                                  
			                                                                   
			target = null
			var recover: int = 1 if _last_safe_x > position.x else -1
			_trace_frame(frames_here, position, on_floor, recover, "recover", false, position, 0.0)
			_apply_direction(recover)
			previous_x = position.x
			continue

		var target_position: Vector2 = _node_position(target)
		var delta_x: float = target_position.x - position.x
		var direction: int = 0
		if absf(delta_x) > ARRIVE_X_TOL:
			direction = 1 if delta_x > 0.0 else -1

		                                      
		if absf(position.x - previous_x) < 0.4:
			stuck_frames += 1
		else:
			stuck_frames = 0
		previous_x = position.x

		var want_jump: bool = false
		var jump_reason: String = ""
		var pending_landing_x: float = NAN

		if not on_floor and _air_committed:
			var to_goal: float = _air_goal_x - position.x
			var air_direction: int = 0
			if absf(to_goal) > AIR_X_TOL:
				air_direction = 1 if to_goal > 0.0 else -1
			_trace_frame(
				frames_here, position, on_floor, air_direction, "commit", false,
				Vector2(_air_goal_x, position.y), to_goal
			)
			_apply_direction(air_direction)
			previous_x = position.x
			continue

		if not on_floor and not _is_over_ground(position):
			                                                                  
			                                                                     
			                                                      
			                                                                    
			                                                                
			var back: float = _last_safe_x - position.x
			direction = 1 if back > 0.0 else -1
			_last_cliff = "rescue"
			_trace_frame(frames_here, position, on_floor, direction, "rescue", false, target_position, delta_x)
			_apply_direction(direction)
			continue

		_last_cliff = "-"
		if on_floor and direction != 0:
			                                                                   
			                                                     
			var probe: Dictionary = _probe_ground_ahead(position, direction)
			var cliff: String = String(probe.get("kind", "ground"))
			_last_cliff = cliff
			if cliff == "gap":
				pending_landing_x = float(probe.get("landing_x", position.x))
				jump_reason = "gap"
				                                                                
				                                                              
				                                                               
				direction = 0
				                                                                
				                                                               
				                                                              
				                                                              
				                                                                
				want_jump = on_floor
			elif cliff == "void":
				                                                
				_abandon(target)
				target = null
				_release_motion()
				_held_direction = ""
				continue

		_apply_direction(direction)

		if on_floor:
			if target_position.y < position.y - JUMP_UP_THRESHOLD and absf(delta_x) <= REACH_X:
				want_jump = true
				jump_reason = "above"
			if stuck_frames >= STUCK_FRAMES:
				stuck_frames = 0
				stuck_jumps += 1
				if stuck_jumps > MAX_STUCK_JUMPS:
					_abandon(target)
					target = null
					_release_motion()
					_held_direction = ""
					continue
				want_jump = true
				jump_reason = "stuck"

		_trace_frame(
			frames_here, position, on_floor, direction, _last_cliff, want_jump,
			target_position, delta_x
		)

		if want_jump and on_floor and _jump_cooldown == 0:
			if is_nan(pending_landing_x):
				pending_landing_x = _choose_landing(position, direction, target_position)
			if jump_reason == "above" and is_equal_approx(pending_landing_x, position.x):
				                                                                
				                                                             
				                             
				want_jump = false
			else:
				_jump_cooldown = JUMP_COOLDOWN_FRAMES
				_air_goal_x = pending_landing_x
				_air_frames = 0
				_air_committed = true
				GBProbe.tap("gb_jump", 1)

	_release_motion()
	_held_direction = ""
	return _failure("inconclusive", "timed out on this level", frames_here)


                                                                            

func _pick_target(is_final: bool, frames_here: int, position: Vector2) -> Dictionary:
	var want_collectibles: bool = is_final or frames_here < COLLECT_BUDGET_FRAMES

	if want_collectibles:
		var best: Node = null
		var best_cost: float = INF
		for node: Node in _visible_collectibles():
			if not _is_eligible(node.get_instance_id()):
				continue
			var node_position: Vector2 = _node_position(node)
			                                                              
			var cost: float = absf(node_position.x - position.x) + 3.0 * absf(node_position.y - position.y)
			if cost < best_cost:
				best_cost = cost
				best = node
		if best != null:
			return {"node": best, "is_goal": false}

	if is_final:
		return {"node": null, "is_goal": false}

	var best_goal: Node = null
	var best_goal_cost: float = INF
	for value: Variant in GBProbe.group_nodes("gb_goal"):
		if not (value is Node) or not is_instance_valid(value):
			continue
		var goal: Node = value as Node
		if not _is_eligible(goal.get_instance_id()):
			continue
		var goal_position: Vector2 = _node_position(goal)
		var goal_cost: float = absf(goal_position.x - position.x) + 3.0 * absf(goal_position.y - position.y)
		if goal_cost < best_goal_cost:
			best_goal_cost = goal_cost
			best_goal = goal
	if best_goal != null:
		return {"node": best_goal, "is_goal": true}
	return {"node": null, "is_goal": false}


                                                                               
                                                                               
                                
func _target_is_valid(target: Variant) -> bool:
	if not (target is Object) or not is_instance_valid(target as Object):
		return false
	var node: Node = target as Node
	if node == null:
		return false
	if not _is_eligible(node.get_instance_id()):
		return false
	if node is CanvasItem and not (node as CanvasItem).visible:
		return false
	return node.is_inside_tree()


func _abandon(target: Variant) -> void:
	if not (target is Object) or not is_instance_valid(target as Object):
		return
	var key: int = (target as Object).get_instance_id()
	_abandon_count[key] = int(_abandon_count.get(key, 0)) + 1
	_cooldown_until[key] = _total_frames + ABANDON_COOLDOWN_FRAMES


func _is_eligible(key: int) -> bool:
	if int(_abandon_count.get(key, 0)) >= MAX_ABANDONS_PER_TARGET:
		return false
	return _total_frames >= int(_cooldown_until.get(key, 0))


                                                                                 
func _has_benched_target() -> bool:
	for node: Node in _visible_collectibles():
		var key: int = node.get_instance_id()
		if int(_abandon_count.get(key, 0)) < MAX_ABANDONS_PER_TARGET:
			return true
	return false


                                                                                

                                                              
                                                             
                                                                   
                                                             
func _probe_ground_ahead(position: Vector2, direction: int) -> Dictionary:
	var player: Node = GBProbe.get_player()
	if not (player is Node2D) or not (player as Node2D).is_inside_tree():
		return {"kind": "ground", "landing_x": position.x}
	var space: PhysicsDirectSpaceState2D = (player as Node2D).get_world_2d().direct_space_state
	if space == null:
		return {"kind": "ground", "landing_x": position.x}

	var exclusions: Array[RID] = []
	if player is CollisionObject2D:
		exclusions.append((player as CollisionObject2D).get_rid())

	if _has_ground_below(space, position + Vector2(direction * LOOKAHEAD_NEAR, 0.0), exclusions):
		return {"kind": "ground", "landing_x": position.x}
	var distance: float = LOOKAHEAD_NEAR + LOOKAHEAD_STEP
	while distance <= LOOKAHEAD_FAR:
		if _has_ground_below(space, position + Vector2(direction * distance, 0.0), exclusions):
			                                                                    
			                                                                    
			                                                                 
			var far_edge: float = distance
			var probe_distance: float = distance + LOOKAHEAD_STEP
			while probe_distance <= LOOKAHEAD_FAR:
				if not _has_ground_below(
					space, position + Vector2(direction * probe_distance, 0.0), exclusions
				):
					break
				far_edge = probe_distance
				probe_distance += LOOKAHEAD_STEP
			var aim: float = distance + minf(LANDING_INSET, (far_edge - distance) * 0.5)
			return {"kind": "gap", "landing_x": position.x + direction * aim}
		distance += LOOKAHEAD_STEP
	return {"kind": "void", "landing_x": position.x}


                                                                              
                                                                               
                                                              
func _choose_landing(position: Vector2, direction: int, target_position: Vector2) -> float:
	var player: Node = GBProbe.get_player()
	if not (player is Node2D) or not (player as Node2D).is_inside_tree():
		return position.x
	var space: PhysicsDirectSpaceState2D = (player as Node2D).get_world_2d().direct_space_state
	if space == null:
		return position.x
	var exclusions: Array[RID] = []
	if player is CollisionObject2D:
		exclusions.append((player as CollisionObject2D).get_rid())

	                                                                  
	var under_target: PhysicsRayQueryParameters2D = PhysicsRayQueryParameters2D.create(
		target_position,
		target_position + Vector2(0.0, GROUND_RAY_LENGTH * 2.0)
	)
	under_target.collide_with_areas = false
	under_target.collide_with_bodies = true
	under_target.exclude = exclusions
	if not space.intersect_ray(under_target).is_empty():
		return target_position.x

	if direction == 0:
		return position.x
	var probe: Dictionary = _probe_ground_ahead(position, direction)
	if String(probe.get("kind", "ground")) == "gap":
		return float(probe.get("landing_x", position.x))
	return position.x


                                                                              
                                                                            
func _is_over_ground(position: Vector2) -> bool:
	var player: Node = GBProbe.get_player()
	if not (player is Node2D) or not (player as Node2D).is_inside_tree():
		return true
	var space: PhysicsDirectSpaceState2D = (player as Node2D).get_world_2d().direct_space_state
	if space == null:
		return true
	var exclusions: Array[RID] = []
	if player is CollisionObject2D:
		exclusions.append((player as CollisionObject2D).get_rid())
	var query: PhysicsRayQueryParameters2D = PhysicsRayQueryParameters2D.create(
		position,
		position + Vector2(0.0, GROUND_RAY_LENGTH * 4.0)
	)
	query.collide_with_areas = false
	query.collide_with_bodies = true
	query.exclude = exclusions
	return not space.intersect_ray(query).is_empty()


func _has_ground_below(
	space: PhysicsDirectSpaceState2D,
	from: Vector2,
	exclusions: Array[RID]
) -> bool:
	var query: PhysicsRayQueryParameters2D = PhysicsRayQueryParameters2D.create(
		from,
		from + Vector2(0.0, GROUND_RAY_LENGTH)
	)
	                                                                          
	query.collide_with_areas = false
	query.collide_with_bodies = true
	query.exclude = exclusions
	return not space.intersect_ray(query).is_empty()


                                                                        

func _apply_direction(direction: int) -> void:
	var wanted: String = ""
	if direction > 0:
		wanted = "gb_right"
	elif direction < 0:
		wanted = "gb_left"
	if wanted == _held_direction:
		return
	if _held_direction != "":
		GBProbe.release(_held_direction)
	if wanted != "":
		GBProbe.press(wanted)
	_held_direction = wanted


                                                                          
                                                                  
func _trace_frame(
	frame: int, position: Vector2, on_floor: bool, direction: int, cliff: String,
	want_jump: bool, target_position: Vector2, delta_x: float
) -> void:
	if not _tracing:
		return
	_record_trace(
		"f=%d pos=(%.1f,%.1f) floor=%s dir=%d cliff=%s jump=%s tgt=(%.1f,%.1f) dx=%.1f safe=%.1f"
		% [
			frame, position.x, position.y, str(on_floor), direction, cliff,
			str(want_jump), target_position.x, target_position.y, delta_x, _last_safe_x
		]
	)


func _record_trace(line: String) -> void:
	_trace.append(line)
	if _trace.size() > 120:
		_trace.remove_at(0)


func _release_motion() -> void:
	GBProbe.release("gb_left")
	GBProbe.release("gb_right")
	GBProbe.release("gb_jump")


                                                                                

func _read_levels() -> Array:
	if not GBProbe.has_method("runtime_interface") or not GBProbe.runtime_interface_ok():
		return []
	var levels: Variant = GBProbe.runtime_interface().get("levels", [])
	return levels if levels is Array else []


func _settle_scene(max_frames: int = 180) -> bool:
	for _i: int in range(max_frames):
		await get_tree().physics_frame
		_total_frames += 1
		var scene: Node = get_tree().current_scene
		if scene != null and scene.is_inside_tree() and not scene.scene_file_path.is_empty():
			await get_tree().physics_frame
			await get_tree().physics_frame
			_total_frames += 2
			return true
	return false


func _world_bottom() -> float:
	var bounds: Dictionary = GBProbe.world_bounds()
	if not bool(bounds.get("valid", false)):
		return 100000.0
	var maximum: Variant = bounds.get("max", {})
	if maximum is Dictionary:
		return float((maximum as Dictionary).get("y", 100000.0))
	return 100000.0


                                                                            

func _result(verdict: String, message: String, detail: Dictionary) -> Dictionary:
	_release_motion()
	var success: bool = verdict == "completed"
	var payload: Dictionary = {
		"verdict": verdict,
		"success": success,
		"message": message,
		"error": "" if success else message,
		"visited": _visited,
		"collected_by_level": _collected_by_level,
		"remaining_visible_collectibles": _visible_collectible_count(),
		"scene_file_path": GBProbe.scene_path(),
		"global_position": _player_position_dictionary(),
		"total_frames": _total_frames,
		"navigation_by_level": _navigation_by_level,
		"measured_physics_by_level": _physics_by_level,
		"detail": detail,
	}
	if _tracing and not success:
		payload["trace"] = _trace.duplicate()
	return payload


func _failure(verdict: String, message: String, frames: int) -> Dictionary:
	return {
		"ok": false,
		"verdict": verdict,
		"error": message,
		"frames": frames,
		"scene_file_path": GBProbe.scene_path(),
		"global_position": _player_position_dictionary(),
		"remaining_visible_collectibles": _visible_collectible_count(),
	}


                                                                              

func _player_position() -> Vector2:
	var player: Node = GBProbe.get_player()
	if player is Node2D:
		return (player as Node2D).global_position
	return Vector2.ZERO


func _player_position_dictionary() -> Dictionary:
	var position: Vector2 = _player_position()
	return {"x": position.x, "y": position.y}


func _player_on_floor() -> bool:
	var state: Dictionary = GBProbe.player_state()
	return bool(state.get("on_floor", false))


func _node_position(node: Node) -> Vector2:
	if node is Node2D:
		return (node as Node2D).global_position
	return Vector2.ZERO


func _visible_collectibles() -> Array[Node]:
	var visible: Array[Node] = []
	for value: Variant in GBProbe.group_nodes("gb_collectible"):
		if value is CanvasItem and is_instance_valid(value) and (value as CanvasItem).visible:
			visible.append(value as Node)
	return visible


func _visible_collectible_count() -> int:
	return _visible_collectibles().size()
