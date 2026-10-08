extends Node
  
                                                                                            
  
                                                                                             
                                                         
  
                  
                                                                                                
                                                                                            
                                                                                           
           
  
                                            
                                                                                              
                                                                                             
                                                                                               
                                                                                               
                                                                            
  
                                                                                               
                                                                                               
                                                                       
  
                                     
                                                                                           
                                                                                           
                                                                                              
                                                                                     
  
                                                                                                
                                                                           
  
                                        
                                                                                              
                                                                
  
                                                
                                              
                                                  
  
                                                                                            
                                                                                                
                                                                                             
                                                                                           
                                                                                                 
                                                                     
  
                                                                                             
                                                                                               
                                                                                              
                                                                                                
                                  
  
                                                                                             
                                                                                              
                                                                                              
                                                                               

const ENTER_DEADLINE := 600                                                             
const SETTLE_FRAMES := 45                                                      
const HOLD_FRAMES := 110                                                                        
                                                                                                
                                                                                               
                                                                                                 
                                                                                              
                                                                                                  
                                                                                             
                            
const IDLE_FRAMES := HOLD_FRAMES
                                                                                               
                                                                                                
                                                                                                
                                            
const BEND_FLOOR := 0.04                                                                          
const BEND_RATIO := 0.30                                                             
                                                                                               
                                                                                                
                                 
const NOISE_MARGIN := 2.0
                                                                                               
                                              
const SIG_FLOOR := 1.0 / float(HOLD_FRAMES)

var _actions: Array[String] = []
var _plan: Array = []
var _step := -1
var _frame := 0
var _total := 0
var _mark := {}
var _segments: Array = []
var _out_path := "user://gb_runtime_probe.json"
var _phase := "enter"
var _entered := false
var _entry_method := "none"
var _pressed_buttons := 0
                                                                                    
                                                          
var _home_pos := Vector3.ZERO
var _home_valid := false
var _restores := 0
var _restore_failures := 0
                                                                                          
                                                                                             
                                               
var _segment_lost_player := false


func _ready() -> void:
	_out_path = _read_arg("--gb-probe-out", _out_path)
	process_mode = Node.PROCESS_MODE_ALWAYS
	for action in InputMap.get_actions():
		var name := String(action)
		if name.begins_with("ui_"):
			continue
		                                                                       
		if name in ["gb_pause", "gb_reset", "pause", "reset", "quit", "restart",
					"menu", "exit", "fullscreen", "screenshot"]:
			continue
		_actions.append(name)
	_actions.sort()
	_plan.append(["idle", ""])
	for a in _actions:
		_plan.append(["hold", a])
		_plan.append(["idle", ""])


                                                      
  
                                                                                         
                                                                                            
                                                                                            
                                                                                               
                                                                                             
                                                                                  
  
                                                                                             
                                                                                            
                                                                                       
                                                                                         
func _fire_action(action: String, pressed: bool) -> void:
	if not InputMap.has_action(action):
		return
	var ev := InputEventAction.new()
	ev.action = action
	ev.pressed = pressed
	ev.strength = 1.0 if pressed else 0.0
	Input.parse_input_event(ev)
	                                                                                       
	                                                                              
	Input.flush_buffered_events()


func _read_arg(key: String, fallback: String) -> String:
	var args := OS.get_cmdline_user_args()
	args.append_array(OS.get_cmdline_args())
	for i in args.size():
		if args[i] == key and i + 1 < args.size():
			return args[i + 1]
		if args[i].begins_with(key + "="):
			return args[i].substr(key.length() + 1)
	return fallback


                                                                                         
                   
                                                                                         

func _players() -> Array:
	var tree := get_tree()
	if tree == null:
		return []
	return tree.get_nodes_in_group("gb_player")


func _visible_buttons(node: Node, depth: int) -> Array:
	var out: Array = []
	if depth > 14:
		return out
	for child in node.get_children():
		if child is BaseButton and child.is_visible_in_tree() and not child.disabled:
			out.append(child)
		out.append_array(_visible_buttons(child, depth + 1))
	return out


func _looks_in_game() -> bool:
	if _players().size() > 0:
		return true
	                                                                                   
	var tree := get_tree()
	if tree == null:
		return false
	return _visible_buttons(tree.root, 0).is_empty() and _world_is_moving()


var _last_sig := 0.0
func _world_is_moving() -> bool:
	var sig := 0.0
	for n in _spatial(get_tree().root, 0):
		if n is Node2D:
			sig += n.global_position.x + n.global_position.y
		elif n is Node3D:
			sig += n.global_position.x + n.global_position.z
	var moving := absf(sig - _last_sig) > 0.01 and _last_sig != 0.0
	_last_sig = sig
	return moving


func _try_enter() -> void:
	var tree := get_tree()
	if tree == null:
		return
	                                                                                         
	                                                                                     
	var buttons := _visible_buttons(tree.root, 0)
	if buttons.size() > 0 and _pressed_buttons < 6:
		var b: BaseButton = buttons[0]
		_pressed_buttons += 1
		if b.has_signal("pressed"):
			b.grab_focus()
			b.emit_signal("pressed")
			_entry_method = "pressed button '%s'" % [
				b.text if "text" in b else String(b.name)]
			return
	                                                                             
	for code in [KEY_ENTER, KEY_SPACE, KEY_KP_ENTER]:
		var ev := InputEventKey.new()
		ev.physical_keycode = code
		ev.keycode = code
		ev.pressed = true
		Input.parse_input_event(ev)
		var up := InputEventKey.new()
		up.physical_keycode = code
		up.keycode = code
		up.pressed = false
		Input.parse_input_event(up)
	                                  
	for a in ["ui_accept", "ui_select", "start", "confirm", "accept", "interact", "gb_action"]:
		_fire_action(a, true)
		_fire_action(a, false)
	if _entry_method == "none":
		_entry_method = "synthetic accept"


                                                                                         
           
                                                                                         

func _spatial(node: Node, depth: int) -> Array:
	var out: Array = []
	if depth > 12:
		return out
	for child in node.get_children():
		if child is Node2D or child is Node3D:
			out.append(child)
		out.append_array(_spatial(child, depth + 1))
	return out


                                                                                            
                                                                                             
                                                                              
func _snapshot() -> Dictionary:
	var tree := get_tree()
	if tree == null:
		return {"ok": false}
	var out := {"ok": true, "sig": _state_signature()}
	var players := _players()
	if players.size() > 0:
		var p = players[0]
		var pos := Vector3.ZERO
		if p is Node2D:
			pos = Vector3(p.global_position.x, p.global_position.y, 0.0)
		elif p is Node3D:
			pos = p.global_position
		out["mode"] = "gb_player"
		out["player"] = pos
		out["nodes"] = {}
		return out
	var nodes := {}
	for n in _spatial(tree.root, 0):
		if n is Node2D:
			nodes[n.get_instance_id()] = Vector3(n.global_position.x, n.global_position.y, 0.0)
		elif n is Node3D:
			nodes[n.get_instance_id()] = n.global_position
	out["mode"] = "world"
	out["player"] = Vector3.ZERO
	out["nodes"] = nodes
	out["ok"] = nodes.size() > 0 or out.sig.count > 0
	return out


                                                                                                
                                                                                              
                                                                                                
                                                                                                
                       
func _state_signature() -> Dictionary:
	var tree := get_tree()
	if tree == null:
		return {"count": 0, "visible": 0, "text": 0}
	var count := 0
	var vis := 0
	var text_hash := 0
	for n in _all_nodes(tree.root, 0):
		count += 1
		if n is CanvasItem and n.is_visible_in_tree():
			vis += 1
		if n is Label or n is RichTextLabel or n is Button:
			var t: String = str(n.text) if "text" in n else ""
			if t.length() > 0:
				text_hash = (text_hash * 31 + t.hash()) % 2147483647
	return {"count": count, "visible": vis, "text": text_hash}


func _all_nodes(node: Node, depth: int) -> Array:
	var out: Array = []
	if depth > 12:
		return out
	for child in node.get_children():
		out.append(child)
		out.append_array(_all_nodes(child, depth + 1))
	return out


                                                                                             
                                                                                               
                                                
func _displacement_vector(a: Dictionary, b: Dictionary) -> Vector3:
	if not a.get("ok", false) or not b.get("ok", false):
		return Vector3.ZERO
	if a.mode == "gb_player" and b.mode == "gb_player":
		return b.player - a.player
	                                                                                          
	                                                                                            
	                                             
	var deltas: Array[float] = []
	for id in a.nodes:
		if b.nodes.has(id):
			deltas.append((b.nodes[id] - a.nodes[id]).length())
	if deltas.is_empty():
		return Vector3.ZERO
	deltas.sort()
	return Vector3(deltas[int(float(deltas.size() - 1) * 0.98)], 0.0, 0.0)


func _sig_delta(a: Dictionary, b: Dictionary) -> int:
	if not a.has("sig") or not b.has("sig"):
		return 0
	                                                                                          
	                                                                                           
	                                                                                    
	return absi(b.sig.count - a.sig.count) + absi(b.sig.visible - a.sig.visible)


func _text_changed(a: Dictionary, b: Dictionary) -> bool:
	if not a.has("sig") or not b.has("sig"):
		return false
	return b.sig.text != a.sig.text


func _physics_process(_delta: float) -> void:
	_total += 1
	_frame += 1

	if _phase == "enter":
		if _looks_in_game():
			_entered = true
			_phase = "settle"
			_frame = 0
			return
		if _total >= ENTER_DEADLINE:
			_phase = "settle"                                                               
			_frame = 0
			return
		if _frame % 30 == 0:
			_try_enter()
		return

	if _phase == "settle":
		if _frame >= SETTLE_FRAMES:
			_phase = "measure"
			_frame = 0
			_capture_home()
			_advance()
		return

	var current: Array = _plan[_step]
	                                                                                            
	                                                                                          
	                                                                 
	if _players().is_empty():
		_segment_lost_player = true
	var span := HOLD_FRAMES if current[0] == "hold" else IDLE_FRAMES
	if _frame >= span:
		_close_segment(current)
		_advance()


                                                                                 
func _player_pos(p: Node) -> Vector3:
	if p is Node2D:
		return Vector3((p as Node2D).global_position.x, (p as Node2D).global_position.y, 0.0)
	if p is Node3D:
		return (p as Node3D).global_position
	return Vector3.ZERO


func _capture_home() -> void:
	var players := _players()
	if players.is_empty():
		return
	_home_pos = _player_pos(players[0])
	_home_valid = true


                                                       
  
                                                                                                
                                                                                             
                                                                                                  
func _restore_home() -> void:
	if not _home_valid:
		return
	var players := _players()
	if players.is_empty():
		                                                                                      
		                                                                                     
		_restore_failures += 1
		return
	var p = players[0]
	if p is Node2D:
		(p as Node2D).global_position = Vector2(_home_pos.x, _home_pos.y)
	elif p is Node3D:
		(p as Node3D).global_position = _home_pos
	else:
		_restore_failures += 1
		return
	if "velocity" in p:
		var v = p.get("velocity")
		if typeof(v) == TYPE_VECTOR2:
			p.set("velocity", Vector2.ZERO)
		elif typeof(v) == TYPE_VECTOR3:
			p.set("velocity", Vector3.ZERO)
	_restores += 1


func _advance() -> void:
	if _step >= 0:
		var prev: Array = _plan[_step]
		if prev[0] == "hold":
			_fire_action(prev[1], false)
	_step += 1
	_frame = 0
	if _step >= _plan.size():
		_finish()
		return
	                                                                                        
	                                                                                          
	                           
	_restore_home()
	var nxt: Array = _plan[_step]
	if nxt[0] == "hold":
		_fire_action(nxt[1], true)
	_mark = _snapshot()


func _close_segment(current: Array) -> void:
	var now := _snapshot()
	var span := HOLD_FRAMES if current[0] == "hold" else IDLE_FRAMES
	var v := _displacement_vector(_mark, now) / float(max(span, 1))
	var sd := _sig_delta(_mark, now)
	_segments.append({
		"kind": current[0], "action": current[1], "span": span,
		"vx": v.x, "vy": v.y, "vz": v.z, "per_frame": v.length(),
		"sig_delta": sd,
		"sig_rate": float(sd) / float(max(span, 1)),
		"text_changed": _text_changed(_mark, now),
		"mode": now.get("mode", "none"),
		"lost_player": _segment_lost_player,
	})
	_segment_lost_player = false


func _finish() -> void:
	                                                                                           
	                                                                                            
	                                                                                         
	                                                                                            
	                                                                                           
	                                            
	var ix := 0.0
	var iy := 0.0
	var iz := 0.0
	var isig := 0.0
	var n_idle := 0
	for s in _segments:
		if s.kind == "idle":
			ix += s.vx
			iy += s.vy
			iz += s.vz
			isig += s.sig_rate
			n_idle += 1
	var idle_v := Vector3.ZERO
	var idle_sig_rate := 0.0
	if n_idle > 0:
		idle_v = Vector3(ix, iy, iz) / float(n_idle)
		idle_sig_rate = isig / float(n_idle)
	var baseline: float = idle_v.length()

	var per_action := {}
	var live := 0
	var state_only := 0
	var unmeasured := 0
	for i in _segments.size():
		var s: Dictionary = _segments[i]
		if s.kind != "hold":
			continue
		                                                                       
		                                                     
		var before: Dictionary = _segments[i - 1] if i > 0 else s
		var after: Dictionary = _segments[i + 1] if i + 1 < _segments.size() else before
		var bv := Vector3(before.vx, before.vy, before.vz)
		var av := Vector3(after.vx, after.vy, after.vz)
		var local_idle_v: Vector3 = (bv + av) * 0.5
		var local_idle_sig: float = (before.sig_rate + after.sig_rate) * 0.5
		                                                                                        
		                                                                                  
		var noise_v: float = (av - bv).length() * 0.5
		var noise_sig: float = absf(after.sig_rate - before.sig_rate) * 0.5

		var bend: float = (Vector3(s.vx, s.vy, s.vz) - local_idle_v).length()
		var motion_bar: float = maxf(maxf(BEND_FLOOR, BEND_RATIO * local_idle_v.length()),
									 NOISE_MARGIN * noise_v)
		                                                                                    
		                                                                                     
		                                                                               
		                                                                                        
		                                                                                     
		                                                         
		var moved_live: bool = bend > motion_bar and s.per_frame > BEND_FLOOR

		var sig_excess: float = s.sig_rate - local_idle_sig
		var state_bar: float = maxf(SIG_FLOOR, NOISE_MARGIN * noise_sig)
		var state_suggests: bool = sig_excess > state_bar

		                                                                      
		 
		                                                                                      
		                                                                                         
		                                                                                   
		                                                                                          
		                                                                                  
		                                                                                         
		                                                                                     
		 
		                                                                                      
		                                                                                         
		                                                                           
		 
		                                                                                        
		                                                                                     
		                                                                                    
		                                                                                    
		                                                                                    
		 
		                                                                                     
		                                                           
		var lost: bool = bool(s.get("lost_player", false)) \
			or bool(before.get("lost_player", false)) \
			or bool(after.get("lost_player", false))

		var is_live: bool = moved_live and not lost
		if lost:
			unmeasured += 1
		elif state_suggests and not moved_live:
			state_only += 1

		per_action[s.action] = {
			"per_frame": s.per_frame, "bend_vs_idle": bend, "threshold": motion_bar,
			"sig_delta": s.sig_delta, "sig_rate": s.sig_rate,
			"local_idle_sig_rate": local_idle_sig, "sig_excess": sig_excess,
			"noise_motion": noise_v, "noise_sig": noise_sig,
			"text_changed": s.text_changed,
			"state_suggests_live": state_suggests,
			"via": ("player_lost (not measured)" if lost
					else ("motion" if moved_live
						  else ("state (not counted)" if state_suggests else "none"))),
			"measured": not lost,
			"live": is_live,
		}
		if is_live:
			live += 1

	var report := {
		"entered_game": _entered,
		"entry_method": _entry_method,
		"actions_tested": _actions,
		"idle_baseline_per_frame": baseline,
		"idle_sig_delta": idle_sig_rate,
		"threshold": maxf(BEND_FLOOR, BEND_RATIO * baseline),
		"per_action": per_action,
		"live_actions": live,
		                                                                                       
		                                                           
		"unmeasured_actions": unmeasured,
		"dead_actions": _actions.size() - live - unmeasured,
		"state_only_actions": state_only,
		"tracked": _segments[0].mode if _segments.size() > 0 else "none",
		"bend_floor": BEND_FLOOR,
		"injection": "InputEventAction via parse_input_event",
		                                                                                     
		                                                                                       
		                                                                                    
		"segment_restores": _restores,
		"restore_failures": _restore_failures,
		"home_captured": _home_valid,
		"probe_version": 3,
	}
	var f := FileAccess.open(_out_path, FileAccess.WRITE)
	if f != null:
		f.store_string(JSON.stringify(report, "  "))
		f.close()
	print("GB_RUNTIME_PROBE_DONE entered=%s live=%d dead=%d" % [_entered, live, _actions.size() - live])
	get_tree().quit()
