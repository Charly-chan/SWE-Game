extends Node
  
                                                           
  
                                                                               
                                                                               
                                                                               
                                                                                
                                         
  
                                                                
                                                                               
                                                                            
                                                                               
                                                                             
                                                                                
                                                                                
                                                                                
                                                          
                                                        
  
                                                          
                                                                               
                                                                          
                                                                          
                                                                               
                                                                               
                                                                              
                                                         
  
         
                                                                        
                                                                            
  
            
                                                                     
                                                                      
                                                                        
                                                                             
                                                                           
                                                                              
                                                                     
  

                                                                         
                                                                          
                                                                              
                                                                                   
                                             
const LIVENESS_WINDOW_S := 2.0

                                                                               
                                                                     
                                             
const DEFAULT_QUIT_AFTER := 1800

                                                                              
                                                 
const INJECT_ATTEMPTS := 120

var _out_dir := ""
var _requested: Array[int] = []
var _quit_after := DEFAULT_QUIT_AFTER
var _inject_path := ""

var _draw_count := 0
var _liveness_count := -1
var _liveness_done := false
var _armed := false
var _refusal := ""
var _display_server := "unknown"
var _captured: Array = []
var _errors: Array = []
var _inject_requested := {}
var _inject_applied: Array = []
var _inject_unapplied: Array = []
var _inject_tries := 0
var _anchor_camera := ""
var _anchor_camera_applied := false
var _audio_buses: Array[String] = []
var _audio_resolved_buses: Array[String] = []
var _audio_missing_buses: Array[String] = []
var _audio_peak_dbfs := -120.0
var _audio_windows := 0
var _audio_audible_windows := 0
var _audio_categories := {}
var _audio_playing := {}
var _audio_discrete_events := 0
                                                                               
                                                                             
                                                                             
                                                                       
var _timing_max := 0
var _frame_us: Array = []
var _last_draw_us := 0
var _manifest_written := false
var _finished := false
var _started_ms := 0


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_started_ms = Time.get_ticks_msec()
	_out_dir = _read_arg("--gb-capture-out", "")
	_quit_after = int(_read_arg("--gb-capture-quit-after", str(DEFAULT_QUIT_AFTER)))
	_inject_path = _read_arg("--gb-inject-state", "")
	_timing_max = int(_read_arg("--gb-capture-timing", "0"))
	var normalized_anchor := _read_arg("--gb-anchor-camera", "")
	var normalized_buses := _read_arg("--gb-audio-buses", "")
	var shared := get_node_or_null("/root/GBHarnessProbe")
	if shared != null and shared.has_method("runtime_interface") and shared.runtime_interface_ok():
		var runtime: Dictionary = shared.runtime_interface()
		var full_anchor := str(runtime.get("anchor_camera", ""))
		normalized_anchor = full_anchor.get_slice("::", 1) if "::" in full_anchor else ""
		normalized_buses = ",".join(PackedStringArray(runtime.get("audio_buses", [])))
	elif _read_arg("--gb-interface-file", "") != "":
		_errors.append("evaluator packaging error: " + (shared.runtime_interface_error() if shared != null else "canonical probe missing"))
	_anchor_camera = normalized_anchor
	for raw_bus in normalized_buses.split(",", false):
		var bus := raw_bus.strip_edges()
		if bus != "":
			_audio_buses.append(bus)
	for bus_name: String in _audio_buses:
		if AudioServer.get_bus_index(bus_name) >= 0:
			_audio_resolved_buses.append(bus_name)
		else:
			_audio_missing_buses.append(bus_name)
			_errors.append("requested audio bus does not exist: " + bus_name)
	for piece in _read_arg("--gb-capture-frames", "").split(",", false):
		var text := piece.strip_edges()
		if text.is_valid_int():
			_requested.append(int(text))
	_requested.sort()
	_display_server = DisplayServer.get_name()

	if _out_dir == "":
		push_error("gb_capture_probe: --gb-capture-out was not given; nothing can be recorded")
		return

	var mk := DirAccess.make_dir_recursive_absolute(_out_dir)
	if mk != OK and not DirAccess.dir_exists_absolute(_out_dir):
		push_error("gb_capture_probe: cannot create output directory %s (error %d)"
			% [_out_dir, mk])
		return

	_load_inject_state()

	                                                                            
	                                                                     
	if RenderingServer.has_signal("frame_post_draw"):
		RenderingServer.frame_post_draw.connect(_on_frame_post_draw)
	else:
		_errors.append("RenderingServer has no frame_post_draw signal")
	var timer := get_tree().create_timer(LIVENESS_WINDOW_S, true, false, true)
	timer.timeout.connect(_on_liveness_window_closed)

	_begin_capture()


func _read_arg(key: String, fallback: String) -> String:
	var args := OS.get_cmdline_user_args()
	args.append_array(OS.get_cmdline_args())
	for i in args.size():
		if args[i] == key and i + 1 < args.size():
			return args[i + 1]
		if args[i].begins_with(key + "="):
			return args[i].substr(key.length() + 1)
	return fallback


                                                                             
     
                                                                             

func _begin_capture() -> void:
	if DisplayServer.get_name() == "headless":
		var why := "REFUSING TO CAPTURE: DisplayServer.get_name() == \"headless\". "
		why += "This process has no rasteriser. get_viewport().get_texture() hands "
		why += "back a non-null object with no pixels behind it and get_image() "
		why += "returns null, so every screenshot written from here would be a file "
		why += "that does not exist. Re-run in X mode: xvfb-run -a -s "
		why += "\"-screen 0 1152x648x24\" <godot> --rendering-driver opengl3 --path "
		why += "<project>, or the GPU wrapper /usr/local/bin/godot-gpu. "
		why += "Requested frames %s -- NONE of them were captured." % str(_requested)
		_refuse(why)
		return
	_armed = true
	print("GB_CAPTURE armed display_server=%s frames=%s out=%s"
		% [_display_server, str(_requested), _out_dir])


func _refuse(reason: String) -> void:
	_refusal = reason
	_armed = false
	                                                                         
	printerr("GB_CAPTURE_REFUSED " + reason)
	print("GB_CAPTURE_REFUSED " + reason)
	push_error("gb_capture_probe: " + reason)
	                                                                       
	                                                                         
	                                                              
	_write_manifest()


                                                                             
                 
                                                                             

func _on_frame_post_draw() -> void:
	_draw_count += 1
	_sample_audio()
	if _timing_max > 0:
		var now := Time.get_ticks_usec()
		if _last_draw_us > 0 and _frame_us.size() < _timing_max:
			_frame_us.append(now - _last_draw_us)
		_last_draw_us = now
	if not _armed:
		return
	if _requested.has(_draw_count):
		_shoot(_draw_count)
	if _draw_count >= _quit_after:
		_finish()
		return
	if _liveness_done and _remaining() == 0:
		_finish()


func _on_liveness_window_closed() -> void:
	_liveness_count = _draw_count
	_liveness_done = true
	print("GB_CAPTURE liveness frame_post_draw fires in %.1fs: %d"
		% [LIVENESS_WINDOW_S, _liveness_count])
	if _refusal != "":
		_finish()
		return
	if _liveness_count == 0:
		var why := "REFUSING TO TRUST THIS CAPTURE: RenderingServer.frame_post_draw "
		why += "fired 0 times in %.1f seconds while DisplayServer reported \"%s\". " \
			% [LIVENESS_WINDOW_S, _display_server]
		why += "The renderer is not drawing. Any PNG written here would record a "
		why += "surface nobody painted."
		_refuse(why)
		_finish()
		return
	if _remaining() == 0:
		_finish()


                                                                             
                                                                          
                                                                               
                                                                            
                     
func _remaining() -> int:
	if _requested.is_empty():
		return -1
	var left := 0
	for f in _requested:
		if not _has_capture(f):
			left += 1
	return left


func _has_capture(frame: int) -> bool:
	for c in _captured:
		if int(c["frame"]) == frame:
			return true
	return false


func _shoot(frame: int) -> void:
	var vp := get_viewport()
	if vp == null:
		_errors.append("frame %d: get_viewport() returned null" % frame)
		return
	var tex := vp.get_texture()
	if tex == null:
		_errors.append("frame %d: viewport texture is null" % frame)
		return
	                                                                        
	                            
	var img := tex.get_image()
	if img == null:
		_errors.append("frame %d: get_texture() was non-null but get_image() returned null -- no pixels behind the texture (DisplayServer=%s)"
			% [frame, _display_server])
		return
	if img.is_empty() or img.get_width() == 0 or img.get_height() == 0:
		_errors.append("frame %d: image is %dx%d"
			% [frame, img.get_width(), img.get_height()])
		return

	                                                                            
	                                                                          
	                        
	var path := _out_dir.path_join("frame_%05d.png" % frame)
	var err := img.save_png(path)
	if err != OK:
		_errors.append("frame %d: save_png(%s) failed with error %d" % [frame, path, err])
		return
	var size := _file_size(path)
	_captured.append({
		"frame": frame,
		"path": path,
		"bytes": size,
		"width": img.get_width(),
		"height": img.get_height(),
		"resolution": "%dx%d" % [img.get_width(), img.get_height()],
		"engine_frames_drawn": Engine.get_frames_drawn(),
		"ms_since_ready": Time.get_ticks_msec() - _started_ms,
	})
	print("GB_CAPTURE shot frame=%d %dx%d %d bytes -> %s"
		% [frame, img.get_width(), img.get_height(), size, path])


func _file_size(path: String) -> int:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return -1
	var n := int(f.get_length())
	f.close()
	return n


                                                                             
                    
                                                                             

                                                                              
                                                                                
                                                                               
                                                             
func _load_inject_state() -> void:
	if _inject_path == "":
		return
	if not FileAccess.file_exists(_inject_path):
		_errors.append("inject state file not found: " + _inject_path)
		return
	var parsed = JSON.parse_string(FileAccess.get_file_as_string(_inject_path))
	if typeof(parsed) != TYPE_DICTIONARY:
		_errors.append("inject state is not a JSON object: " + _inject_path)
		return
	_inject_requested = parsed
	for key in _inject_requested.keys():
		_inject_unapplied.append(str(key))


func _apply_inject() -> void:
	var tree := get_tree()
	if tree == null:
		return
	if not _inject_requested.has("player_position"):
		return
	var shared := get_node_or_null("/root/GBHarnessProbe")
	if shared == null:
		_errors.append("canonical GBHarnessProbe autoload is missing")
		return
	var players: Array = shared.group_nodes("gb_player")
	if players.is_empty():
		return
	var v = _inject_requested["player_position"]
	if typeof(v) != TYPE_ARRAY or v.size() < 2:
		return
	var p = players[0]
	if p is Node2D:
		p.global_position = Vector2(float(v[0]), float(v[1]))
		_mark_applied("player_position")
	elif p is Node3D and v.size() >= 3:
		p.global_position = Vector3(float(v[0]), float(v[1]), float(v[2]))
		_mark_applied("player_position")


func _mark_applied(key: String) -> void:
	_inject_unapplied.erase(key)
	if not _inject_applied.has(key):
		_inject_applied.append(key)


func _process(_delta: float) -> void:
	_apply_anchor_camera()
	if not _armed or _inject_requested.is_empty():
		return
	if _inject_tries >= INJECT_ATTEMPTS or not _inject_applied.is_empty():
		return
	_inject_tries += 1
	_apply_inject()


func _apply_anchor_camera() -> void:
	if _anchor_camera == "" or _anchor_camera_applied:
		return
	var tree := get_tree()
	if tree == null or tree.current_scene == null:
		return
	var camera := tree.current_scene.get_node_or_null(NodePath(_anchor_camera))
	if camera is Camera3D or camera is Camera2D:
		camera.make_current()
		_anchor_camera_applied = true


func _sample_audio() -> void:
	if _audio_resolved_buses.is_empty():
		return
	_audio_windows += 1
	var audible := false
	for bus_name: String in _audio_resolved_buses:
		var index := AudioServer.get_bus_index(bus_name)
		if index < 0:
			continue
		var peak := maxf(
			AudioServer.get_bus_peak_volume_left_db(index, 0),
			AudioServer.get_bus_peak_volume_right_db(index, 0))
		_audio_peak_dbfs = maxf(_audio_peak_dbfs, peak)
		if peak > -60.0:
			audible = true
			_audio_categories[bus_name] = int(_audio_categories.get(bus_name, 0)) + 1
	if audible:
		_audio_audible_windows += 1
	var tree := get_tree()
	if tree == null or tree.root == null:
		return
	                                                                        
	                                                                          
	                                                            
	var stack: Array[Node] = [tree.root]
	var now_playing := {}
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		for child: Node in node.get_children():
			stack.append(child)
		if node is AudioStreamPlayer or node is AudioStreamPlayer2D or node is AudioStreamPlayer3D:
			if bool(node.get("playing")):
				var key := int(node.get_instance_id())
				now_playing[key] = true
				if not _audio_playing.has(key):
					_audio_discrete_events += 1
	_audio_playing = now_playing


                                                                             
          
                                                                             

                                                                              
                                                                             
                                                                             
func _png_json_ratio() -> Dictionary:
	var png := 0
	var js := 0
	for entry in _list_files(_out_dir):
		var low := str(entry).to_lower()
		if low.ends_with(".png"):
			png += 1
		elif low.ends_with(".json"):
			js += 1
	return {
		"png": png,
		"json": js,
		"ratio": (float(png) / float(js)) if js > 0 else -1.0,
		"visual_evidence_exists": png > 0,
	}


func _list_files(dir_path: String) -> Array:
	var out: Array = []
	var d := DirAccess.open(dir_path)
	if d == null:
		return out
	d.list_dir_begin()
	var entry := d.get_next()
	while entry != "":
		if d.current_is_dir():
			if entry != "." and entry != "..":
				out.append_array(_list_files(dir_path.path_join(entry)))
		else:
			out.append(entry)
		entry = d.get_next()
	d.list_dir_end()
	return out


func _write_manifest() -> void:
	if _out_dir == "":
		return
	var missed: Array = []
	for f in _requested:
		if not _has_capture(f):
			missed.append(f)
	var got: Array = []
	for c in _captured:
		got.append(c["frame"])
	var tree := get_tree()
	var scene_path := ""
	if tree != null and tree.current_scene != null:
		scene_path = tree.current_scene.scene_file_path
	var manifest := {
		"probe": "gb_capture_probe.gd",
		"display_server": _display_server,
		"refused": _refusal != "",
		"refusal_reason": _refusal,
		                                                                        
		                                                              
		"frame_post_draw_count_2s": _liveness_count,
		"liveness_window_closed": _liveness_done,
		"frame_post_draw_total": _draw_count,
		"liveness_window_s": LIVENESS_WINDOW_S,
		"requested_frames": _requested,
		"captured_frames": got,
		"missed_frames": missed,
		"captures": _captured,
		"errors": _errors,
		"quit_after": _quit_after,
		"inject_state_requested": _inject_requested,
		"inject_state_applied": _inject_applied,
		"inject_state_unapplied": _inject_unapplied,
		"anchor_camera": _anchor_camera,
		"anchor_camera_applied": _anchor_camera_applied,
		"audio_buses": _audio_buses,
		"audio_resolved_buses": _audio_resolved_buses,
		"audio_missing_buses": _audio_missing_buses,
		"audio_peak_dbfs": _audio_peak_dbfs if not _audio_resolved_buses.is_empty() else null,
		"audio_duty_cycle": (float(_audio_audible_windows) / float(_audio_windows)) if _audio_windows > 0 else null,
		"audio_discrete_events": _audio_discrete_events if not _audio_resolved_buses.is_empty() else null,
		"audio_categories": _audio_categories,
		"engine_frames_drawn": Engine.get_frames_drawn(),
		"frame_us": _frame_us,
		"scene_file": scene_path,
		"elapsed_ms": Time.get_ticks_msec() - _started_ms,
		"png_json_ratio": _png_json_ratio(),
	}
	var path := _out_dir.path_join("capture_manifest.json")
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f == null:
		printerr("gb_capture_probe: cannot write manifest to " + path)
		return
	f.store_string(JSON.stringify(manifest, "  "))
	f.close()
	_manifest_written = true


func _finish() -> void:
	                                                                       
	                                                                        
	                                                                          
	                               
	if _finished:
		return
	_finished = true
	_write_manifest()
	print("GB_CAPTURE_DONE captured=%d requested=%d refused=%s draws=%d"
		% [_captured.size(), _requested.size(), str(_refusal != ""), _draw_count])
	var tree := get_tree()
	if tree != null:
		tree.quit()


func _exit_tree() -> void:
	                                                                       
	                                                                           
	                                              
	if not _manifest_written and _out_dir != "":
		_write_manifest()
