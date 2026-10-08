extends Node
  
                                                                   
  
                                   
                                                                           
                                                                               
                                                                              
                                                                               
                                                                               
                                                                            
                                        
  
                                                                             
                                                                             
                                                                              
                                                                               
                                                                           
                                                  
  
                                                             
                                                   
                                                                       
                                                                               
                                  
                                                                   
  
                                     
                                                                               
                                                                                
                                                                           
                                                                                   
                                                                               
                                                                              
                                                                            
                                                          
  
                                                                              
                                                                               
                                                                              
                                                                                
  
                                                  
                                                                              
                                                                                
                                                                                
                                      
  
                                  
                                                                           
                                                                               
                                                                             
                                                                              
                                                                            
                                                                             
                                                                              
                                                                           
                                                                           
                                                                             
  

                                                                        
                                                                                
                                                                              
const PROBE_PATH := "/root/GBTruthProbe"

                                                                               
                                                                          
const ACTIONS := ["gb_left", "gb_right", "gb_up", "gb_down",
	"gb_jump", "gb_action", "gb_pause", "gb_reset"]

                                                                               
                                                                 
const NUMERIC_PROPERTIES := ["score", "health", "hp", "lives", "shields", "combo",
	"inventory", "coins", "ammo", "energy", "shield", "kills", "charge",
	"fuse_left", "heat", "score_earned", "cells_delivered", "clock_left",
	"progress", "timer", "coins_taken"]

                                                                           
                                                                                  
                                
const NUMERIC_HOLDERS := ["run", "state", "run_state", "game"]

                                                                               
                                                                      
                                                                          
                                    
var _numeric_mapping := {}
var _declared_prop_names: Array = []
var _numeric_source := {}
var _behavior_plan := {}
var _collect_plan := {}
var _goal_plan := {}
var _cycle_node: Node = null
var _cycle_parent := ""
var _cycle_effect := {}
var _pick_elapsed := 0
var _pressed_action := ""
var _resource_before: Variant = null

                                                           
                                                                                
                                                                        
                                                                              
                                                                            
                                                                               
                                                                         
                                                                              
                                                                            
                                                                               
  
                                                                              
                                                                               
                                                                              
                        
const ENTRY_ACTIONS := ["ui_accept", "gb_action", "gb_jump"]

                                                                              
                                                                                
                              
const MAX_FRAMES := 5400

var _out := ""
var _declared_level := ""
var _settle := 60
var _dwell := 12
var _watch := 120
var _max_cycles := 40
var _series_interval := 8
var _probe_sha := ""

var _phase := "settle"
var _left := 0
var _frames := 0
var _entry_tries := 0
var _entry_method := "none"
var _errors: Array = []
var _finished := false

var _base := {}
var _collect := {}
var _goal := {}
var _scene_at_base := ""

                                                                               
var _series: Array = []
var _series_armed := false

                                                                              
                                                                             
                                                    
var _cycles: Array = []
var _visited: Dictionary = {}
var _cycle_target := ""
var _cycle_before := 0
var _cycles_capped := false

                                                                     
  
                                                                               
                                                                                
                                                                             
                                                                                
                                                                                
                                                                        
                                                                            
                                                          
var _hold: Node = null


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_out = _arg("--gb-truth-out", "")
	_declared_level = _arg("--gb-truth-level", "")
	_settle = int(_arg("--gb-truth-settle", "60"))
	_dwell = int(_arg("--gb-truth-dwell", "12"))
	_watch = int(_arg("--gb-truth-watch", "120"))
	_max_cycles = int(_arg("--gb-truth-cycles", "40"))
	_series_interval = maxi(1, int(_arg("--gb-truth-series-interval", "8")))
	_probe_sha = _arg("--gb-truth-probe-sha", "")
	var plan: Variant = JSON.parse_string(_arg("--gb-truth-behavior-plan", "{}"))
	if plan is Dictionary:
		_behavior_plan = plan
	_collect_plan = _behavior_plan.get("collect", {})
	_goal_plan = _behavior_plan.get("goal", {})
	_left = _settle
	_load_numeric_mapping()
	if _out == "":
		push_error("gb_truth_driver: --gb-truth-out was not given; nothing can be recorded")
	get_tree().set_auto_accept_quit(true)


func _load_numeric_mapping() -> void:
                                                                              
	                                                                             
	var shared := get_node_or_null(PROBE_PATH)
	if shared == null or not shared.has_method("runtime_interface"):
		_errors.append("canonical probe has no evaluator runtime interface")
		return
	if not shared.runtime_interface_ok():
		_errors.append("evaluator packaging error: " + shared.runtime_interface_error())
		return
	var parsed: Dictionary = shared.runtime_interface()
	var mapping: Variant = parsed.get("numeric", {})
	if not mapping is Dictionary:
		return
	_numeric_mapping = mapping
	for slot in mapping.keys():
		var prop := str(mapping[slot]).strip_edges()
		if prop != "" and not _declared_prop_names.has(prop):
			_declared_prop_names.append(prop)


func _numeric_names() -> Array:
	var names: Array = []
	for p in NUMERIC_PROPERTIES:
		names.append(p)
	for p in _declared_prop_names:
		if not names.has(p):
			names.append(p)
	return names


func _finalize_numeric(numeric: Dictionary) -> Dictionary:
	                                                                        
	                                                                  
	                                                                
	_numeric_source = {}
	var declared := {}
	for slot in _numeric_mapping.keys():
		declared[str(_numeric_mapping[slot])] = str(slot)
	for key in numeric.keys():
		if declared.has(key) or _numeric_mapping.has(key):
			_numeric_source[key] = "declared"
		else:
			_numeric_source[key] = "guess"
	for slot in _numeric_mapping.keys():
		var prop := str(_numeric_mapping[slot])
		if numeric.has(prop):
			numeric[str(slot)] = numeric[prop]
			_numeric_source[str(slot)] = "declared"
	return numeric


func _arg(key: String, fallback: String) -> String:
	var args := OS.get_cmdline_user_args()
	args.append_array(OS.get_cmdline_args())
	for i in args.size():
		if args[i] == key and i + 1 < args.size():
			return args[i + 1]
		if args[i].begins_with(key + "="):
			return args[i].substr(key.length() + 1)
	return fallback


func _probe() -> Node:
	return get_node_or_null(PROBE_PATH)


func _physics_process(_delta: float) -> void:
	if _finished:
		return
	_frames += 1
	if _frames > MAX_FRAMES:
		_errors.append("driver hit its %d-frame ceiling in phase %s" % [MAX_FRAMES, _phase])
		_finish("frame_ceiling")
		return
	                                                                          
	                                                                            
	                                                                        
	                                                             
	if _phase != "settle" and _check_transition():
		return
	_hold_on_target()
	_maybe_sample_series()
	match _phase:
		"pick":
			_tick_pick()
		"deliver":
			_tick_deliver()
		"goal":
			_tick_goal()
		"settle":
			_tick_settle()


                                                                             

func _tick_settle() -> void:
	_left -= 1
	if _left > 0:
		return
	var probe := _probe()
	if probe == null:
		_errors.append("the injected probe is not at " + PROBE_PATH)
		_finish("no_probe")
		return
	                                                                            
	                                                                           
	                                                                          
	if not _has_player(probe) and _entry_tries < ENTRY_ACTIONS.size():
		var action: String = ENTRY_ACTIONS[_entry_tries]
		_entry_tries += 1
		_press_once(probe, action)
		_entry_method = action
		_left = _settle
		return
	if not _has_player(probe):
		_entry_method = "none"
	_base = _read(probe)
	_scene_at_base = String(_base.get("scene_path", ""))
	_series_armed = true
	_append_series_sample(probe)
	_collect = {
		"attempted": false,
		"target": "",
		"alive_before": _alive_count(_base, "gb_collectible"),
		"alive_after": _alive_count(_base, "gb_collectible"),
		"delta": 0,
		"detail": "",
	}
	_goal = {
		"attempted": false,
		"target": "",
		"scene_before": _scene_at_base,
		"scene_after": _scene_at_base,
		"changed": false,
		"frames_to_change": -1,
		"detail": "",
		"effect": {"kind": _goal_plan.get("effect", "observable_progress"),
			"complete_trigger": _goal_plan.get("complete_trigger", false),
			"observation_missing": _goal_plan.get("effect", "") == "progress_increase" and not _numeric_mapping.has("progress"),
			"progress_before": _base.get("numeric", {}).get("progress", null)},
	}
	_begin_pick(probe)


func _has_player(probe: Node) -> bool:
	var state: Dictionary = probe.player_state()
	return bool(state.get("exists", false))


                                                                                
                                                                                
                                                         
func _press_once(probe: Node, action: String) -> void:
	if InputMap.has_action(action):
		probe.press(action)
		probe.release(action)


                                                                             

                                                                                 
                                                                             
                                                                             
                                                                           
func _read(probe: Node) -> Dictionary:
	var snap: Dictionary = probe.snapshot()
	var groups: Dictionary = snap.get("groups", {})
	var world: Dictionary = {}
	for group: String in groups.keys():
		var points: Array = []
		for value: Variant in probe.group_nodes(group):
			if not value is Node:
				continue
			var node: Node = value
			if not is_instance_valid(node) or not node.is_inside_tree():
				continue
			var pos: Variant = _global_position(node)
			if pos is Vector2:
				points.append([pos.x, pos.y, 0.0])
			elif pos is Vector3:
				points.append([pos.x, pos.y, pos.z])
		world[group] = points
	var bound: Array = []
	for action: String in ACTIONS:
		if InputMap.has_action(action):
			bound.append(action)
	return {
		"frame": int(snap.get("frame", 0)),
		"scene_path": String(snap.get("scene", {}).get("path", "")),
		"player": snap.get("player", {}),
		"bounds": snap.get("world", {}).get("bounds", {}),
		"groups": groups,
		"world_positions": world,
		"bound_actions": bound,
		"collectible_events": snap.get("collectible_events", []),
		"numeric": _read_numeric(probe),
	}


                                                                         
                                                                        
                                                                       
func _read_numeric(probe: Node) -> Dictionary:
	var numeric := {}
	var seen: Dictionary = {}
	var sources: Array = []
	var tree := get_tree()
	if tree != null and tree.root != null:
		for child: Node in tree.root.get_children():
			sources.append(child)
		if tree.current_scene != null:
			sources.append(tree.current_scene)
	if probe != null:
		for raw_group: Variant in probe.group_names():
			var group := String(raw_group)
			for value: Variant in probe.group_nodes(group):
				if value is Node:
					sources.append(value)
	for n: Variant in sources:
		if not n is Node:
			continue
		var node: Node = n
		if node == self or not is_instance_valid(node):
			continue
		var nid: int = node.get_instance_id()
		if seen.has(nid):
			continue
		seen[nid] = true
		_absorb_numeric(node, numeric)
		for holder: String in NUMERIC_HOLDERS:
			var bag: Variant = node.get(holder)
			if bag is Object and not bag is Node:
				var bid: int = (bag as Object).get_instance_id()
				if seen.has(bid):
					continue
				seen[bid] = true
				_absorb_numeric(bag, numeric)
	return _finalize_numeric(numeric)


func _absorb_numeric(obj: Object, numeric: Dictionary) -> void:
	if obj == null:
		return
	for prop: String in _numeric_names():
		var v: Variant = obj.get(prop)
		if typeof(v) == TYPE_INT or typeof(v) == TYPE_FLOAT:
			numeric[prop] = float(numeric.get(prop, 0.0)) + float(v)
			if prop == "coins_taken":
				numeric["coins"] = float(numeric.get("coins", 0.0)) + float(v)
	if obj.has_method("earned_score"):
		var earned: Variant = obj.call("earned_score")
		if typeof(earned) == TYPE_INT or typeof(earned) == TYPE_FLOAT:
			numeric["score"] = float(numeric.get("score", 0.0)) + float(earned)


func _maybe_sample_series() -> void:
	if not _series_armed or _series_interval <= 0:
		return
	if (_frames % _series_interval) != 0:
		return
	_append_series_sample(_probe())


func _append_series_sample(probe: Node) -> void:
	if probe == null:
		return
	var snap: Dictionary = probe.snapshot()
	var player: Dictionary = snap.get("player", {})
	var pos: Variant = player.get("position", {})
	var alive: Dictionary = {}
	var groups: Dictionary = snap.get("groups", {})
	for group: String in groups.keys():
		var entry: Variant = groups[group]
		if entry is Dictionary:
			alive[group] = int(entry.get("alive", 0))
	_series.append({
		"frame": _frames,
		"phase": _phase,
		"player": pos,
		"numeric": _read_numeric(probe),
		"numeric_source": _numeric_source.duplicate(),
		"scene_path": String(snap.get("scene", {}).get("path", "")),
		"alive": alive,
	})


func _global_position(node: Node) -> Variant:
	if node is Node2D:
		return (node as Node2D).global_position
	if node is Control:
		return (node as Control).global_position
	if node is Node3D:
		return (node as Node3D).global_position
	return null


                                                                                
                                                        
  
                                                                     
                                                                              
                                                                                
                                                                             
                                                                                
                                                                            
                                                                           
                                                                        
  
                                                                                
                                                                             
                                                                    
func _contact_point(node: Node) -> Variant:
	var points: Array = []
	_collect_own_shapes(node, node, points)
	if points.is_empty():
		return _global_position(node)
	var sum := Vector3.ZERO
	for point: Vector3 in points:
		sum += point
	var centre: Vector3 = sum / float(points.size())
	if node is Node2D or node is Control:
		return Vector2(centre.x, centre.y)
	return centre


func _collect_own_shapes(node: Node, root: Node, out: Array) -> void:
	for child: Node in node.get_children():
		if child != root and (child is CollisionObject2D or child is CollisionObject3D):
			continue
		if child is CollisionShape2D or child is CollisionPolygon2D:
			var flat: Variant = _global_position(child)
			if flat is Vector2:
				out.append(Vector3(flat.x, flat.y, 0.0))
		elif child is CollisionShape3D or child is CollisionPolygon3D:
			var solid: Variant = _global_position(child)
			if solid is Vector3:
				out.append(solid)
		_collect_own_shapes(child, root, out)


                                                                             

                                                                          
func _begin_pick(probe: Node) -> void:
	if _cycles.size() >= _max_cycles:
		_cycles_capped = true
		_begin_goal(probe, "the cycle ceiling was reached")
		return
	var target := _next_collectible(probe)
	if target == null:
		_begin_goal(probe, "every collectible in this level has been visited")
		return
	var pos: Variant = _contact_point(target)
	if pos == null or not probe.teleport(pos):
		_visited[target.get_instance_id()] = true
		_errors.append("could not teleport onto collectible " + String(target.name))
		_begin_pick(probe)
		return
	_visited[target.get_instance_id()] = true
	_cycle_target = String(target.name)
	_cycle_before = _alive_count(_read(probe), "gb_collectible")
	_cycle_node = target
	_cycle_parent = str(target.get_parent().get_path())
	_cycle_effect = {"kind": _collect_plan.get("effect", "target_changed"),
		"complete_trigger": _collect_plan.get("complete_trigger", false),
		"observed": false, "actions": _collect_plan.get("actions", []),
		"cycle_index": _cycles.size()}
	_resource_before = _resource_amount(probe)
	_pick_elapsed = 0
	if _cycles.is_empty():
		_collect["attempted"] = true
		_collect["target"] = _cycle_target
		_collect["alive_before"] = _cycle_before
		_collect["alive_after"] = _cycle_before
		_collect["detail"] = "collection activation started on %s; a complete cycle result has not yet been recorded" % _cycle_target
		_collect["effect"] = _cycle_effect.duplicate(true)
		_collect["effect"]["observation_missing"] = true
	_hold = target
	_phase = "pick"
	_left = _dwell


func _tick_pick() -> void:
	var probe := _probe()
	if probe == null:
		_finish("no_probe")
		return
	_pick_elapsed += 1
	if _pressed_action != "":
		probe.release(_pressed_action)
		_pressed_action = ""
	var actions: Array = _collect_plan.get("actions", [])
	for i in actions.size():
		if _pick_elapsed == 3 + i * 4:
			var action := str(actions[i])
			if InputMap.has_action(action):
				probe.press(action)
				_pressed_action = action
			else:
				_cycle_effect["activation_missing"] = action
	_observe_collect_effect(probe)
	_left -= 1
	if _left > 0:
		return
	if _goal_plan.get("effect", "") in ["route_progress", "route_campaign"]:
		_close_cycle(probe, false)
		_begin_pick(probe)
		return
	                                                                        
	                                                                          
	var goal := _first_alive(probe, "gb_goal")
	if goal == null:
		_close_cycle(probe, false)
		_begin_pick(probe)
		return
	var pos: Variant = _contact_point(goal)
	_goal["effect"]["progress_before"] = _read_numeric(probe).get("progress", null)
	if pos == null or not probe.teleport(pos):
		_close_cycle(probe, false)
		_begin_pick(probe)
		return
	_goal["attempted"] = true
	_goal["target"] = String(goal.name)
	_hold = goal
	_phase = "deliver"
	_left = _dwell


func _tick_deliver() -> void:
	var probe := _probe()
	if probe == null:
		_finish("no_probe")
		return
	_left -= 1
	if _left > 0:
		return
	_close_cycle(probe, true)
	_begin_pick(probe)


func _close_cycle(probe: Node, delivered: bool) -> void:
	_observe_collect_effect(probe)
	var after: int = _alive_count(_read(probe), "gb_collectible")
	var record := {
		"index": _cycles.size(),
		"target": _cycle_target,
		"delivered": delivered,
		"alive_before": _cycle_before,
		"alive_after": after,
		"delta": after - _cycle_before,
	}
	_cycles.append(record)
	                                                                     
	                                                                        
	                                                                         
	                                                                           
	if _cycles.size() == 1:
		_collect["attempted"] = true
		_collect["target"] = _cycle_target
		_collect["alive_before"] = _cycle_before
		_collect["alive_after"] = after
		_collect["delta"] = after - _cycle_before
		_collect["detail"] = (
			"one collect-and-deliver cycle on %s: %d alive before, %d after"
			% [_cycle_target, _cycle_before, after]
		)
		_collect["effect"] = _cycle_effect.duplicate(true)


func _resource_amount(probe: Node) -> Variant:
	var slot := str(_collect_plan.get("slot", ""))
	if slot != "":
		if not _numeric_mapping.has(slot):
			return null
		return _read_numeric(probe).get(slot, null)
	return null


func _observe_collect_effect(probe: Node) -> void:
	if _cycle_effect.is_empty() or probe.scene_path() != _scene_at_base:
		return
	if _cycle_effect.get("kind", "") == "numeric_increase":
		var after: Variant = _resource_amount(probe)
		_cycle_effect["before"] = _resource_before
		_cycle_effect["after"] = after
		_cycle_effect["slot"] = _collect_plan.get("slot", "")
		_cycle_effect["observation_missing"] = _resource_before == null or after == null
		if _resource_before == null or after == null:
			return
		var changed := float(after) > float(_resource_before)
		if changed:
			_cycle_effect["observed"] = true
	else:
		var changed := not is_instance_valid(_cycle_node) or not _cycle_node.is_inside_tree()
		if not changed:
			changed = str(_cycle_node.get_parent().get_path()) != _cycle_parent or not _cycle_node.is_in_group("gb_collectible")
			var visible_value: Variant = _cycle_node.get("visible")
			if typeof(visible_value) == TYPE_BOOL and not bool(visible_value):
				changed = true
		if changed:
			_cycle_effect["observed"] = true
	                                                                     
	                                                                         
	                                                                
	if bool(_cycle_effect.get("observed", false)) and (_cycles.is_empty() or not bool(_collect.get("effect", {}).get("observed", false))):
		var after := _alive_count(_read(probe), "gb_collectible")
		_collect["attempted"] = true
		_collect["target"] = _cycle_target
		_collect["alive_before"] = _cycle_before
		_collect["alive_after"] = after
		_collect["delta"] = after - _cycle_before
		_collect["effect"] = _cycle_effect.duplicate(true)
		_collect["detail"] = "collection effect observed on %s before delivery: %d alive before, %d after" % [_cycle_target, _cycle_before, after]


                                                                             

func _begin_goal(probe: Node, why: String) -> void:
	if _collect.get("detail", "") == "":
		_collect["detail"] = "this level declares no gb_collectible, so there is nothing to pick up"
	if _goal_plan.get("effect", "") in ["route_progress", "route_campaign"]:
		_goal["detail"] = "goal has prerequisite actions; evaluated by certified input-only progress routes"
		_finish("goal_requires_route")
		return
	var target := _first_alive(probe, "gb_goal")
	if target == null:
		_goal["detail"] = "this level declares no gb_goal, so there is no transition to observe"
		_finish("no_goal")
		return
	var pos: Variant = _contact_point(target)
	_goal["effect"]["progress_before"] = _read_numeric(probe).get("progress", null)
	if pos == null or not probe.teleport(pos):
		_goal["detail"] = "the player could not be teleported onto " + String(target.name)
		_finish("goal_unreachable")
		return
	_goal["attempted"] = true
	_goal["target"] = String(target.name)
	_goal["detail"] = why
	_hold = target
	_phase = "goal"
	_left = _watch


func _tick_goal() -> void:
	var probe := _probe()
	if probe == null:
		_finish("no_probe")
		return
	_left -= 1
	if _left > 0:
		return
	_goal["scene_after"] = probe.scene_path()
	_goal["detail"] = (
		"%d collect-and-deliver cycles then %d frames on %s, and the scene path never changed"
		% [_cycles.size(), _watch, String(_goal.get("target", ""))]
	)
	_finish("goal_no_transition")


                                                                              
                                                                
func _check_transition() -> bool:
	var probe := _probe()
	if probe == null:
		return false
	var now: String = probe.scene_path()
	if bool(_goal.get("attempted", false)) and _numeric_mapping.has("progress"):
		var before: Variant = _goal.get("effect", {}).get("progress_before", null)
		var after: Variant = _read_numeric(probe).get("progress", null)
		_goal["effect"]["observation_missing"] = before == null or after == null
		if before != null and after != null and float(after) > float(before):
			_goal["effect"]["observed"] = true
			_goal["effect"]["progress_after"] = after
			_goal["detail"] = "declared progress increased from %s to %s after goal activation" % [before, after]
			                                                                  
			                                                                   
			                                                                    
			                                                                
			if now == _scene_at_base and _goal_plan.get("effect", "") == "progress_increase":
				_finish("goal_progress")
				return true
	if now == "" or now == _scene_at_base:
		return false
	_goal["scene_after"] = now
	_goal["changed"] = true
	_goal["frames_to_change"] = _frames
	_goal["detail"] = "%s -> %s after %d collect-and-deliver cycles" % [
		_scene_at_base, now, _cycles.size()
	]
	_finish("goal_transition")
	return true


                                                                             

                                                               
  
                                                                             
                                                                               
                                                                              
           
func _hold_on_target() -> void:
	if _hold == null:
		return
	if not is_instance_valid(_hold) or not _hold.is_inside_tree():
		_hold = null
		return
	var probe := _probe()
	if probe == null:
		return
	var pos: Variant = _contact_point(_hold)
	if pos != null:
		probe.teleport(pos)


                                                                
  
                                                                             
                                                                               
                                                           
func _next_collectible(probe: Node) -> Node:
	for value: Variant in probe.group_nodes("gb_collectible"):
		if not value is Node:
			continue
		var node: Node = value
		if _visited.has(node.get_instance_id()):
			continue
		if is_instance_valid(node) and node.is_inside_tree():
			return node
	return null


func _first_alive(probe: Node, group: String) -> Node:
	for value: Variant in probe.group_nodes(group):
		if not value is Node:
			continue
		var node: Node = value
		if is_instance_valid(node) and node.is_inside_tree():
			var visible_value: Variant = node.get(&"visible")
			if typeof(visible_value) == TYPE_BOOL and not bool(visible_value):
				continue
			return node
	return null


func _alive_count(reading: Dictionary, group: String) -> int:
	var groups: Dictionary = reading.get("groups", {})
	var entry: Dictionary = groups.get(group, {})
	return int(entry.get("alive", 0))


                                                                             

func _finish(reason: String) -> void:
	if _finished:
		return
	_finished = true
	var probe := _probe()
	if probe != null and _pressed_action != "":
		probe.release(_pressed_action)
	if probe != null and _series_armed:
		_append_series_sample(probe)
	                                                                         
	                                                                      
	                                                                    
	                                                                       
	                                                            
	var final_numeric: Dictionary = {}
	var final_source: Dictionary = {}
	for i in range(_series.size() - 1, -1, -1):
		var sample: Dictionary = _series[i]
		var bag: Dictionary = sample.get("numeric", {})
		if not bag.is_empty():
			final_numeric = bag
			final_source = sample.get("numeric_source", {})
			if final_source.is_empty():
				final_source = _numeric_source
			break
	if final_numeric.is_empty() and probe != null:
		final_numeric = _read_numeric(probe)
		final_source = _numeric_source
	_numeric_source = final_source
	_write({
		"driver": "gb_truth_driver.gd",
		"probe_sha256": _probe_sha,
		"engine": "%s.%s.%s" % [
			Engine.get_version_info().get("major", 0),
			Engine.get_version_info().get("minor", 0),
			Engine.get_version_info().get("patch", 0),
		],
		"declared_level": _declared_level,
		"stop_reason": reason,
		"entry_method": _entry_method,
		"settle_frames": _settle,
		"dwell_frames": _dwell,
		"watch_frames": _watch,
		"series_interval": _series_interval,
		"driver_frames": _frames,
		"base": _base,
		"collect": _collect,
		"cycles": _cycles,
		"cycles_capped": _cycles_capped,
		"goal": _goal,
		"series": _series,
		"numeric": final_numeric,
		"numeric_source": _numeric_source,
		"errors": _errors,
	})
	var tree := get_tree()
	if tree != null:
		tree.quit()


func _write(payload: Dictionary) -> void:
	if _out == "":
		return
	                                                                            
	                                                             
	var tmp := _out + ".part"
	var file := FileAccess.open(tmp, FileAccess.WRITE)
	if file == null:
		printerr("gb_truth_driver: cannot write " + tmp)
		return
	file.store_string(JSON.stringify(payload))
	file.flush()
	file.close()
	DirAccess.rename_absolute(tmp, _out)
	print("GB_TRUTH_DONE reason=%s scene=%s frames=%d" % [
		String(payload.get("stop_reason", "")), _scene_at_base, _frames])


func _exit_tree() -> void:
	                                                                       
	                                                                      
	                                                              
	if not _finished:
		_errors.append("the tree exited during phase " + _phase)
		_finish("tree_exited")
