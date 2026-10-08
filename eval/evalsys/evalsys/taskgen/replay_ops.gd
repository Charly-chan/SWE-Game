extends SceneTree

                                                                             
                                                                               
 
                                                                          
                                                                     
                                                                            
                                                                           
                                                                         
                                                                              
                                                                            
                                                                            
                                                                             
                                                                      
                                                                             
                                                                           
                            

const DEFAULT_FRAMES := {
	"noop": 1,
	"wait": 30,
	"tap": 6,
	"hold": 30,
	"state": 1,
	"release": 1,
}
const TAIL_FRAMES := 30
const BOOT_DEADLINE := 900
const CANONICAL_ACTIONS := ["gb_left", "gb_right", "gb_up", "gb_down", "gb_jump", "gb_action"]
const SUCCESS_ENDINGS := ["victory", "success", "win", "complete", "run_complete"]
const FAILURE_ENDINGS := ["defeat", "failure", "failed", "loss", "lose"]
                                                                 
const LEVELS_ACCEPTED_FORM := ("each levels entry must be a \"res://…\" scene-path string naming a shipped scene "
	+ "(no objects, no {id, name, scene} records)")

var _driver: Driver = null


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	if args.is_empty():
		_fail("usage: ... -s interface/replay_ops.gd -- /absolute/path/to/ops.json")
		return
	var parsed = JSON.parse_string(FileAccess.get_file_as_string(String(args[0])))
	if typeof(parsed) == TYPE_DICTIONARY:
		parsed = parsed.get("ops", [])
	if typeof(parsed) != TYPE_ARRAY:
		_fail("ops file must be a JSON array or an object with an ops array")
		return
	var manifest := _manifest()
	var bad_levels := _unresolved_levels(manifest)
	if not bad_levels.is_empty():
		                                                              
		# unresolved_levels_detail): the evaluator's verdict on this manifest.
		print("REPLAY_WARNING gb_levels.json invalid: unresolved entries: "
			+ ", ".join(bad_levels) + "; " + LEVELS_ACCEPTED_FORM
			+ " -- the evaluator's interface loader rejects this manifest, so the card "
			+ "would be evaluation_incomplete; fix gb_levels.json before submitting.")
	var scene_path := _first_declared_level(manifest)
	if scene_path == "":
		print("REPLAY_WARNING res://gb_levels.json is missing or declares no valid first level; "
			+ "the evaluator cannot run its witness without it. Falling back to run/main_scene.")
		scene_path = String(ProjectSettings.get_setting("application/run/main_scene", ""))
	if scene_path == "":
		_fail("project has no first gb_levels.json level or run/main_scene")
		return
	var packed := load(scene_path) as PackedScene
	if packed == null:
		_fail("could not load start scene: " + scene_path)
		return
	_driver = Driver.new()
	_driver.name = "GBReplayOps"
	_driver.configure(self, parsed, manifest)
	                                                                            
	                                                                
	root.add_child(_driver)
	var scene := packed.instantiate()
	root.add_child(scene)
	current_scene = scene
	_driver.note_start_scene()


func _manifest() -> Dictionary:
	if not FileAccess.file_exists("res://gb_levels.json"):
		return {}
	var manifest = JSON.parse_string(FileAccess.get_file_as_string("res://gb_levels.json"))
	return manifest if typeof(manifest) == TYPE_DICTIONARY else {}


                                                                            
                                                                  
                                                                       
                                                                              
                          
func _level_valid(value) -> bool:
	if typeof(value) != TYPE_STRING:
		return false
	var scene := String(value)
	return scene.begins_with("res://") and FileAccess.file_exists(scene)


func _json_type_name(value) -> String:
	match typeof(value):
		TYPE_NIL:
			return "null"
		TYPE_BOOL:
			return "boolean"
		TYPE_INT, TYPE_FLOAT:
			return "number"
		TYPE_STRING:
			return "string"
		TYPE_ARRAY:
			return "array"
		TYPE_DICTIONARY:
			return "object"
		_:
			return type_string(typeof(value))


                                                                            
func _describe_bad_level(index: int, value) -> String:
	if typeof(value) != TYPE_STRING:
		return "levels[%d] is a JSON %s, not a string" % [index, _json_type_name(value)]
	var scene := String(value)
	if not scene.begins_with("res://"):
		return "levels[%d] '%s' is not a res:// path" % [index, scene]
	return "levels[%d] '%s' does not name a shipped scene file" % [index, scene]


func _unresolved_levels(manifest: Dictionary) -> PackedStringArray:
	var out := PackedStringArray()
	var levels = manifest.get("levels", [])
	if typeof(levels) != TYPE_ARRAY:
		return out
	for index in range(levels.size()):
		if not _level_valid(levels[index]):
			out.append(_describe_bad_level(index, levels[index]))
	return out


func _first_declared_level(manifest: Dictionary) -> String:
	var levels = manifest.get("levels", [])
	if typeof(levels) != TYPE_ARRAY or levels.is_empty():
		return ""
	return String(levels[0]) if _level_valid(levels[0]) else ""


func _fail(message: String) -> void:
	push_error("REPLAY_ERROR " + message)
	print("REPLAY_ERROR " + message)
	quit(2)


class Driver extends Node:
	var _tree: SceneTree
	var _ops: Array = []
	var _op_i := -1
	var _op_left := 0
	var _held: Dictionary = {}
	var _edge_flush_pending := false
	var _phase := "boot"
	var _boot := 0
	var _settle_left := 0
	var _tail_left := TAIL_FRAMES
	var _tick := 0
	var _run_ticks := 0
	var _last_scene := ""
	var _allowed: Dictionary = {}
	var _ending_of_scene: Dictionary = {}
	var _warned: Dictionary = {}

	func configure(tree: SceneTree, ops: Array, manifest: Dictionary) -> void:
		_tree = tree
		_ops = ops
		for action in CANONICAL_ACTIONS:
			_allowed[action] = true
		var extras = manifest.get("extended_actions", [])
		if typeof(extras) == TYPE_ARRAY:
			for item in extras:
				var ident := String(item.get("id", "")) if typeof(item) == TYPE_DICTIONARY else String(item)
				if ident != "":
					_allowed[ident] = true
		var endings = manifest.get("endings", {})
		if typeof(endings) == TYPE_DICTIONARY:
			for key in endings.keys():
				if SUCCESS_ENDINGS.has(String(key)) or FAILURE_ENDINGS.has(String(key)):
					_ending_of_scene[String(endings[key])] = String(key)

	func _ready() -> void:
		                                                                      
		process_mode = Node.PROCESS_MODE_ALWAYS

	func note_start_scene() -> void:
		_last_scene = _scene_path()
		print("REPLAY_SCENE frame=0 scene=%s" % _last_scene)

	func _physics_process(_delta: float) -> void:
		_tick += 1
		var scene := _scene_path()
		if scene != _last_scene:
			_last_scene = scene
			var suffix := ""
			if _ending_of_scene.has(scene):
				suffix = " ending=%s" % _ending_of_scene[scene]
			print("REPLAY_SCENE frame=%d tape_frame=%d scene=%s%s" % [_tick, _run_ticks, scene, suffix])
		match _phase:
			"boot":
				_tick_boot()
			"settle":
				_tick_settle()
			"run":
				_tick_run()

	                                                                       
	                                                                       
	                                                                        
	func _tick_boot() -> void:
		_boot += 1
		if _player() != null:
			_phase = "settle"
			return
		if _boot >= BOOT_DEADLINE:
			_fail("no alive gb_player node appeared within %d physics frames; "
				% BOOT_DEADLINE + "the evaluator's route driver stops here with no_player")

	func _tick_settle() -> void:
		_settle_left -= 1
		if _settle_left <= 0:
			_phase = "run"

	func _tick_run() -> void:
		_run_ticks += 1
		_advance_ops()

	                                                 
	func _advance_ops() -> void:
		if _op_left > 0:
			_op_left -= 1
			return
		if _op_i + 1 >= _ops.size():
			_release_all(true)
			if _tail_left > 0:
				_tail_left -= 1
				return
			print("REPLAY_FINAL frame=%d tape_frame=%d scene=%s" % [_tick, _run_ticks, _scene_path()])
			_tree.quit(0)
			return
		_op_i += 1
		var op = _ops[_op_i]
		if typeof(op) != TYPE_DICTIONARY:
			_fail("op %d is not an object" % _op_i)
			return
		var kind := String(op.get("op", ""))
		if not DEFAULT_FRAMES.has(kind):
			_fail("op %d has unsupported kind: %s" % [_op_i, kind])
			return
		var actions: Array = []
		if typeof(op.get("actions")) == TYPE_ARRAY:
			actions = op.get("actions")
		elif String(op.get("action", "")) != "":
			actions = [op.get("action")]
		var frames := clampi(int(op.get("frames", DEFAULT_FRAMES[kind])), 1, 600)
		if kind == "state":
			var flush_edges := _edge_flush_pending
			_edge_flush_pending = false
			var desired := {}
			for raw_action in actions:
				var action := String(raw_action)
				if _action_ok(action):
					desired[action] = true
			for raw_action in _held.keys():
				var action := String(raw_action)
				if not desired.has(action):
					_release_action(action)
					_held.erase(action)
			for raw_action in desired.keys():
				var action := String(raw_action)
				if not _held.has(action):
					_press_action(action)
					_held[action] = true
			if flush_edges:
				Input.flush_buffered_events()
			_op_left = frames - 1
			return
		var flush_edges := _release_all(false)
		match kind:
			"hold", "tap":
				for raw_action in actions:
					var action := String(raw_action)
					if _action_ok(action):
						_press_action(action)
						_held[action] = true
			"release":
				for raw_action in actions:
					var action := String(raw_action)
					if InputMap.has_action(action):
						_release_action(action)
						_held.erase(action)
			_:
				pass
		                                                                      
		                                                                    
		                             
		if kind == "tap" and frames == 1:
			flush_edges = true
			_edge_flush_pending = true
		if flush_edges:
			Input.flush_buffered_events()
		_op_left = frames - 1

	func _action_ok(action: String) -> bool:
		if not InputMap.has_action(action):
			_warn_once(action, "action '%s' is not in this project's InputMap; ignored" % action)
			return false
		if not _allowed.has(action):
			_warn_once(action, "action '%s' is outside the gb_* / extended_actions dispatch set; "
				% action + "the evaluator ignores it too")
			return false
		return true

	func _warn_once(key: String, message: String) -> void:
		if _warned.has(key):
			return
		_warned[key] = true
		print("REPLAY_WARNING " + message)

	func _release_all(flush_if_required: bool) -> bool:
		var flush_edges := _edge_flush_pending
		_edge_flush_pending = false
		for action in _held.keys():
			if InputMap.has_action(String(action)):
				_release_action(String(action))
		_held.clear()
		if flush_if_required and flush_edges:
			Input.flush_buffered_events()
			return false
		return flush_edges

	func _press_action(action: String) -> void:
		Input.action_press(action)
		_queue_action_event(action, true)

	func _release_action(action: String) -> void:
		Input.action_release(action)
		_queue_action_event(action, false)

	func _queue_action_event(action: String, pressed: bool) -> void:
		var event := InputEventAction.new()
		event.action = StringName(action)
		event.pressed = pressed
		event.strength = 1.0 if pressed else 0.0
		Input.parse_input_event(event)

	func _player() -> Node:
		for n in get_tree().get_nodes_in_group("gb_player"):
			if _alive(n):
				return n
		return null

	func _alive(n: Node) -> bool:
		if n == null or not is_instance_valid(n) or not n.is_inside_tree():
			return false
		var visible = n.get(&"visible")
		if typeof(visible) == TYPE_BOOL:
			return bool(visible)
		return true

	func _scene_path() -> String:
		var scene := _tree.current_scene
		return scene.scene_file_path if scene != null else ""

	func _fail(message: String) -> void:
		push_error("REPLAY_ERROR " + message)
		print("REPLAY_ERROR " + message)
		_tree.quit(2)
