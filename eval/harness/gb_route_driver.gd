extends Node
                                                                           
  
                                                                           
                                                                              
                                                                             
                                                                               
                                                                           
                                                  

const NUMERIC_PROPERTIES := ["score", "health", "hp", "lives", "shields", "combo",
    "inventory", "coins", "ammo", "energy", "shield", "kills", "charge",
    "fuse_left", "heat", "score_earned", "cells_delivered", "clock_left",
    "progress", "timer", "coins_taken"]
const NUMERIC_HOLDERS := ["run", "state", "run_state", "game"]
const WALK_DEPTH := 12
const ENTRY_ACTIONS := ["ui_accept", "confirm", "start", "gb_action"]

var _numeric_mapping := {}
var _device_ids_mapping := {}
var _declared_prop_names = []
var _numeric_source := {}

var _plan := {}
var _out_path := "user://gb_route.json"
var _io_dir := ""
var _interactive := false
var _video_start_gate := ""

var _rows: Array = []
var _phase := "boot"
var _stop := ""
var _boot := 0
var _settle_left := 0
var _setup_frames := 0
var _tail_left := 0
var _budget := 3600
var _sig_interval := 10

var _ops: Array = []
var _main_ops: Array = []
var _setup_ops: Array = []
var _op_i := -1
var _op_left := 0
var _idle := false
var _held := {}
var _axis_held := {}
var _edge_flush_pending := false
var _steps := 0
var _turns := 0
var _max_turns := 32
var _await_agent := false
var _await_frames := 0
var _agent_deadline := 36000

var _injects := 0
var _deaths := 0
var _stall := 0
var _errors: Array = []
var _out_of_bounds := 0
var _had_player := false
var _player_seen := false
var _requested_scene := ""
var _start_scene := ""
var _whole_game := false
var _declared_levels: Array = []
var _level_entry := {}
var _success_scenes: Array = []
var _failure_scenes: Array = []
var _continuation_scenes: Array = []
var _ending_vocab := false
var _visited_levels := {}
var _last_level_entry_debug := ""
var _last_scene := ""
var _whole_game_clear := false
var _whole_game_outcome := ""
var _success_before_all_levels := false
var _group_totals := {}
var _device_index := {}
var _device_contacts := {}
var _device_nodes := {}
var _device_by_instance := {}
var _device_event_counters := {}
var _stop_overlap: Array = []
var _stop_collect: Array = []
var _goal_row := -1
var _audio_events := 0
var _audio_last := 0
var _last_pos := Vector3.ZERO
var _bounds_min := Vector3.ZERO
var _bounds_max := Vector3.ZERO
var _finished := false


var _plan_path := ""
var _early_autoload_injected := false


func _ready() -> void:
    process_mode = Node.PROCESS_MODE_ALWAYS
    var shared_probe := _probe()
    if shared_probe != null and shared_probe.has_method("set_passive_utility_mode"):
        shared_probe.set_passive_utility_mode(true)
    _load_numeric_mapping()
                                                                            
                                                                           
                                                                           
    _plan_path = OS.get_environment("GB_ROUTE_PLAN")
    if _plan_path == "":
        _plan_path = _arg("--gb-route-plan", "")
    _plan_path = _plan_path.replace(char(92), "/")
    var env_out := OS.get_environment("GB_ROUTE_OUT")
    _out_path = env_out if env_out != "" else _arg("--gb-route-out", _out_path)
    _out_path = _out_path.replace(char(92), "/")
                                                                         
                                                                            
                                                                          
                                                                             
                                                                        
    var early_json := OS.get_environment("GB_ROUTE_AUTOLOAD_STATE")
    if early_json != "":
        var early = JSON.parse_string(early_json)
        if _apply_autoload_state_inject(early):
            _injects += 1
            _early_autoload_injected = true
                                                                           
                                                                             
                                                                        
    get_tree().set_auto_accept_quit(true)


func _apply_plan_file() -> void:
                                                                           
                                                                            
    if _plan_path == "" or not _plan.is_empty():
        return
    print("gb_route apply_plan path=%s" % _plan_path)
    var path := _plan_path.replace(char(92), "/")
    if not FileAccess.file_exists(path) and not FileAccess.file_exists(_plan_path):
        _errors.append("plan file missing: " + _plan_path)
        return
    var text := FileAccess.get_file_as_string(path)
    if text == "":
        text = FileAccess.get_file_as_string(_plan_path)
    if text == "":
        _errors.append("plan file empty/unreadable: " + _plan_path)
        return
    var parsed = JSON.parse_string(text)
    if typeof(parsed) == TYPE_DICTIONARY:
        _plan = parsed
    else:
        _errors.append("plan file did not parse: " + _plan_path)
        return
    _budget = int(_plan.get("budget_frames", 3600))
    _settle_left = int(_plan.get("settle", 30))
    _tail_left = int(_plan.get("tail", 30))
    _sig_interval = maxi(1, int(_plan.get("sig_interval", 10)))
    _interactive = bool(_plan.get("interactive", false))
    _idle = bool(_plan.get("idle", false))
    _io_dir = String(_plan.get("io_dir", "")).replace(char(92), "/")
    _max_turns = int(_plan.get("max_turns", 32))
    _agent_deadline = int(_plan.get("agent_deadline_frames", 36000))
    _stop_overlap = _plan.get("stop_overlap", [])
    _stop_collect = _plan.get("stop_collect", [])
    for entry in _plan.get("ops", []):
        _main_ops.append(entry)
    for entry in _plan.get("setup_ops", []):
        _setup_ops.append(entry)
    _ops = _setup_ops.duplicate() if not _setup_ops.is_empty() else _main_ops.duplicate()
    _requested_scene = String(_plan.get("level", ""))
    _whole_game = bool(_plan.get("whole_game", false))
    _declared_levels = _plan.get("declared_levels", [])
    _level_entry = _plan.get("level_entry", {})
    _success_scenes = _plan.get("success_scenes", [])
    _failure_scenes = _plan.get("failure_scenes", [])
    _continuation_scenes = _plan.get("continuation_scenes", [])
    _ending_vocab = bool(_plan.get("ending_vocab", false))
    var inject = _plan.get("inject", {})
    if typeof(inject) == TYPE_DICTIONARY and inject.has("autoload_state") \
            and not _early_autoload_injected:
        if _apply_autoload_state_inject(inject["autoload_state"]):
            _injects += 1
        else:
            _errors.append("inject.autoload_state could not be applied completely")
    _video_start_gate = String(_plan.get("video_start_gate", ""))
    if _video_start_gate != "" and not FileAccess.file_exists(_video_start_gate):
        _phase = "video_start_gate"
        get_tree().paused = true
    print("gb_route plan_applied ops=%d level=%s interactive=%s" % [
        _main_ops.size(), _requested_scene, str(_interactive)])


func _arg(key: String, fallback: String) -> String:
    var args := OS.get_cmdline_user_args()
    args.append_array(OS.get_cmdline_args())
    for i in args.size():
        if args[i] == key and i + 1 < args.size():
            return args[i + 1]
        if args[i].begins_with(key + "="):
            return args[i].substr(key.length() + 1)
    return fallback


func _load_numeric_mapping() -> void:
    var shared := _probe()
    if shared == null or not shared.has_method("runtime_interface"):
        _errors.append("canonical probe has no evaluator runtime interface")
        return
    if not shared.runtime_interface_ok():
        _errors.append("evaluator packaging error: " + shared.runtime_interface_error())
        return
    var parsed: Dictionary = shared.runtime_interface()
    var mapping = parsed.get("numeric", {})
    if typeof(mapping) == TYPE_DICTIONARY:
        _numeric_mapping = mapping
        for slot in mapping.keys():
            var prop := str(mapping[slot]).strip_edges()
            if prop != "" and not _declared_prop_names.has(prop):
                _declared_prop_names.append(prop)
    var device_ids = parsed.get("device_ids", {})
    if typeof(device_ids) == TYPE_DICTIONARY:
        _device_ids_mapping = device_ids


func _numeric_names() -> Array:
    var names: Array = []
    for p in NUMERIC_PROPERTIES:
        names.append(p)
    for p in _declared_prop_names:
        if not names.has(p):
            names.append(p)
    return names


func _level_entry_value():
    if typeof(_level_entry) != TYPE_DICTIONARY \
            or String(_level_entry.get("kind", "")) != "scene_plus_state":
        return null
    var selector := String(_level_entry.get("selector", ""))
    var parts := selector.split(".", false)
    if parts.size() < 2:
        return null
                                                                          
                                                                             
                                                                             
                                                                         
    var node: Object = get_node_or_null("/root/" + parts[0])
    if node != null:
        var value: Variant = node
        var complete := true
        for i in range(1, parts.size()):
            if not (value is Object):
                complete = false
                break
            var obj := value as Object
            var property := parts[i]
            var found := false
            for info in obj.get_property_list():
                if String(info.get("name", "")) == property:
                    found = true
                    break
            if not found:
                complete = false
                break
            value = obj.get(property)
        if complete:
            return value
    var player := _player()
    var leaf := parts[parts.size() - 1]
    if player != null:
        for info in player.get_property_list():
            if String(info.get("name", "")) == leaf:
                return player.get(leaf)
    return null


func _level_entry_index(value) -> int:
    if typeof(_level_entry) != TYPE_DICTIONARY:
        return -1
    var values = _level_entry.get("values", [])
    if typeof(values) != TYPE_ARRAY:
        return -1
    for index in values.size():
        var expected = values[index]
        var same := str(expected) == str(value)
        if typeof(expected) in [TYPE_INT, TYPE_FLOAT] \
                and typeof(value) in [TYPE_INT, TYPE_FLOAT]:
            same = is_equal_approx(float(expected), float(value))
        if same:
            return index
    return -1


func _required_level_count() -> int:
    if typeof(_level_entry) == TYPE_DICTIONARY \
            and String(_level_entry.get("kind", "")) == "scene_plus_state":
        var values = _level_entry.get("values", [])
        if typeof(values) == TYPE_ARRAY and not values.is_empty():
            return values.size()
    return _declared_levels.size()


func _scene_is_level(scene: String) -> bool:
    if _declared_levels.has(scene):
        return true
    return typeof(_level_entry) == TYPE_DICTIONARY \
        and String(_level_entry.get("scene", "")) == scene


func _mark_level_visit(scene: String) -> void:
    if not _scene_is_level(scene):
        return
    if typeof(_level_entry) == TYPE_DICTIONARY \
            and String(_level_entry.get("kind", "")) == "scene_plus_state" \
            and String(_level_entry.get("scene", "")) == scene:
        var value = _level_entry_value()
        var values = _level_entry.get("values", [])
        var debug := "%s|%s|%s" % [scene, str(value), str(values)]
        if debug != _last_level_entry_debug:
            _last_level_entry_debug = debug
            print("gb_route level_entry " + debug)
        if typeof(values) == TYPE_ARRAY and value != null:
            var index := _level_entry_index(value)
            if index >= 0:
                _visited_levels["%s#%d" % [scene, index + 1]] = true
                return
    _visited_levels[scene] = true


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


                                                                             

func _probe() -> Node:
    return get_node_or_null("/root/GBHarnessProbe")


func _groups() -> Array:
    var probe := _probe()
    return probe.group_names() if probe != null else []


func _nodes(group: String) -> Array:
    var probe := _probe()
    return probe.group_nodes(group) if probe != null else []


func _alive(n: Node) -> bool:
    var probe := _probe()
    return bool(probe.node_is_alive(n)) if probe != null else false


func _player() -> Node:
    for n in _nodes("gb_player"):
        if _alive(n):
            return n
    return null


func _pos(n: Node) -> Vector3:
    if n is Node2D:
        var p: Vector2 = (n as Node2D).global_position
        return Vector3(p.x, p.y, 0.0)
    if n is Node3D:
        return (n as Node3D).global_position
    if n is Control:
        var c: Vector2 = (n as Control).global_position
        return Vector3(c.x, c.y, 0.0)
    return Vector3.ZERO


func _vel(n: Node) -> Vector3:
    var v = n.get("velocity")
    if v is Vector2:
        return Vector3(v.x, v.y, 0.0)
    if v is Vector3:
        return v
    return Vector3.ZERO


func _scene_path() -> String:
    var tree := get_tree()
    if tree == null or tree.current_scene == null:
        return ""
    return tree.current_scene.scene_file_path


func _all_nodes(node: Node, depth: int, out: Array) -> void:
    if depth > WALK_DEPTH:
        return
    for child in node.get_children():
        out.append(child)
        _all_nodes(child, depth + 1, out)


                                                                             
  
                                                                               
                                                                             
                                                                               
                                                                          

func _rect_of(n: Node) -> Rect2:
    var centre := _pos(n)
    var half := Vector2(8.0, 8.0)
    var objects: Array = []
    _collect_2d(n, objects)
    if objects.size() > 0:
        var co: CollisionObject2D = objects[0]
        for oid in co.get_shape_owners():
            for si in co.shape_owner_get_shape_count(oid):
                var sh: Shape2D = co.shape_owner_get_shape(oid, si)
                if sh != null:
                    var r: Rect2 = sh.get_rect()
                    half = r.size * 0.5 * co.global_scale.abs()
                    break
            break
    return Rect2(Vector2(centre.x, centre.y) - half, half * 2.0)


func _collect_2d(node: Node, out: Array) -> void:
    if node is CollisionObject2D:
        out.append(node)
        return
    for child in node.get_children():
        _collect_2d(child, out)


func _touching(player: Node, target: Node) -> bool:
    if target == player:
        return false
    if target is Area2D:
        var a2: Area2D = target as Area2D
        if a2.monitoring:
            for b in a2.get_overlapping_bodies():
                if b == player:
                    return true
            for a in a2.get_overlapping_areas():
                if a == player or (a as Node).is_ancestor_of(player):
                    return true
    if target is Area3D:
        var a3: Area3D = target as Area3D
        if a3.monitoring:
            for b in a3.get_overlapping_bodies():
                if b == player:
                    return true
    if player is Node2D and target is Node2D:
        return _rect_of(player).intersects(_rect_of(target), true)
    return _pos(player).distance_to(_pos(target)) <= 1.5


func _overlaps(player: Node) -> Dictionary:
    var out := {}
    if player == null:
        for g in _groups():
            out[g] = []
        return out
    for g in _groups():
        var names: Array = []
        if g == "gb_player":
            out[g] = names
            continue
        var seen := 0
        for n in _nodes(g):
            seen += 1
            if seen > 200:
                break
            if not _alive(n):
                continue
            if _touching(player, n):
                names.append(String(n.name))
        out[g] = names
    return out


                                                                             
  
                                                                             
                                                                           
                                                                             
                                                                           
                                                                                
                                                                             
                                                                          
                                                                     
  
                                                                    

func _signature() -> Dictionary:
    var tree := get_tree()
    if tree == null:
        return {}
    var nodes: Array = []
    _all_nodes(tree.root, 0, nodes)
    var count := 0
    var visible_count := 0
    var text_hash := 0
    var anim_hash := 0
    var playing := 0
    for n in nodes:
        if n == self:
            continue
        count += 1
        if n is CanvasItem and (n as CanvasItem).is_visible_in_tree():
            visible_count += 1
        if n is Label or n is RichTextLabel or n is Button:
            var t = n.get("text")
            if typeof(t) == TYPE_STRING and String(t).length() > 0:
                text_hash = (text_hash * 31 + String(t).hash()) % 2147483647
        if n is AnimationPlayer:
            anim_hash = (anim_hash * 31
                + String((n as AnimationPlayer).current_animation).hash()) % 2147483647
        if n is AnimatedSprite2D:
            var sprite := n as AnimatedSprite2D
            anim_hash = (anim_hash * 31 + String(sprite.animation).hash()
                + sprite.frame) % 2147483647
        if n is AudioStreamPlayer or n is AudioStreamPlayer2D or n is AudioStreamPlayer3D:
            if bool(n.get("playing")):
                playing += 1
    if playing > _audio_last:
        _audio_events += playing - _audio_last
    _audio_last = playing

    return {
        "node_count": count,
        "visible_count": visible_count,
        "text": text_hash,
        "anim": anim_hash,
        "audio_playing": playing,
        "audio_events": _audio_events,
        "numeric": _numeric_bag(),
    }


func _numeric_bag() -> Dictionary:
    var numeric := {}
    var seen := {}
    var tree := get_tree()
    if tree == null:
        return numeric
    var sources: Array = []
    if tree.root != null:
        for child in tree.root.get_children():
            sources.append(child)
    if tree.current_scene != null:
        sources.append(tree.current_scene)
    for g in _groups():
        for n in _nodes(g):
            sources.append(n)
    for n in sources:
        if n == self or not is_instance_valid(n):
            continue
        var nid: int = n.get_instance_id()
        if seen.has(nid):
            continue
        seen[nid] = true
        _absorb_numeric(n, numeric)
        for holder in NUMERIC_HOLDERS:
            var bag = n.get(holder)
            if typeof(bag) == TYPE_OBJECT and bag != null and not (bag is Node):
                var bid: int = bag.get_instance_id()
                if seen.has(bid):
                    continue
                seen[bid] = true
                _absorb_numeric(bag, numeric)
    return _finalize_numeric(numeric)


func _bounds_from_groups() -> Dictionary:
                                                                                  
    var probe := _probe()
    return probe.group_bounds_3d() if probe != null else {}


func _absorb_numeric(obj, numeric: Dictionary) -> void:
    if obj == null:
        return
    for prop in _numeric_names():
        var v = obj.get(prop)
        if typeof(v) == TYPE_INT or typeof(v) == TYPE_FLOAT:
            numeric[prop] = float(numeric.get(prop, 0.0)) + float(v)
            if prop == "coins_taken":
                numeric["coins"] = float(numeric.get("coins", 0.0)) + float(v)
    if obj.has_method("earned_score"):
        var earned = obj.call("earned_score")
        if typeof(earned) == TYPE_INT or typeof(earned) == TYPE_FLOAT:
            numeric["score"] = float(numeric.get("score", 0.0)) + float(earned)


                                                                             
  
                                                                            
                                                                           
                                                                          
                                                                            
                                 

func _normal_kind(raw: String) -> String:
    var compact := raw.to_lower().replace("_", "").replace("-", "")
    var trimmed := ""
    for ch in compact:
        if not String(ch).is_valid_int():
            trimmed += String(ch)
    return trimmed if trimmed != "" else "device"


func _current_level_no() -> int:
    var scene := _scene_path()
    var index := _declared_levels.find(scene)
    if typeof(_level_entry) == TYPE_DICTIONARY \
            and String(_level_entry.get("scene", "")) == scene:
        var values = _level_entry.get("values", [])
        if typeof(values) == TYPE_ARRAY:
            index = _level_entry_index(_level_entry_value())
    return index + 1 if index >= 0 else 0


func _register_device(device_id: String, n: Node) -> void:
    var instance := int(n.get_instance_id())
    _device_index[device_id] = String(n.name)
    _device_nodes[device_id] = n
    _device_by_instance[instance] = device_id

func _build_device_index() -> void:
    _device_index.clear()
    _device_nodes.clear()
    _device_by_instance.clear()
    _device_event_counters.clear()
                                                                            
                                                                        
                                                                          
    var counts := {}
    var seen := {}
    var current_level_no := _current_level_no()
    var level_prefix := "L%d/" % current_level_no if _whole_game and current_level_no > 0 else ""
    var scene := get_tree().current_scene
    for raw_id in _device_ids_mapping:
        var declared_id := String(raw_id)
        var base_id := declared_id
        if declared_id.begins_with("L") and declared_id.contains("/"):
            var slash := declared_id.find("/")
            var declared_level := int(declared_id.substr(1, slash - 1))
            if declared_level != current_level_no:
                continue
            base_id = declared_id.substr(slash + 1)
        var output_id := level_prefix + base_id
        var node_path := String(_device_ids_mapping[raw_id])
        var declared_node = scene.get_node_or_null(NodePath(node_path)) if scene != null else null
        if declared_node == null:
            _errors.append("device_ids.%s does not resolve in %s: %s" % [
                declared_id, _scene_path(), node_path])
            continue
        if _device_index.has(output_id):
            _errors.append("device_ids collision after level selection: " + output_id)
            continue
        seen[int(declared_node.get_instance_id())] = true
        _register_device(output_id, declared_node)
    for g in _groups():
        if g == "gb_player":
            continue
        for n in _nodes(g):
            if not is_instance_valid(n):
                continue
            var instance := int(n.get_instance_id())
            if seen.has(instance):
                continue
            seen[instance] = true
            var kind: String = String(g).trim_prefix("gb_")
            var index := int(counts.get(kind, 0))
            var device_id := "%s%s#%d" % [level_prefix, kind, index]
            while _device_index.has(device_id):
                index += 1
                device_id = "%s%s#%d" % [level_prefix, kind, index]
            counts[kind] = index + 1
            _register_device(device_id, n)


func _note_contacts(overlaps: Dictionary) -> Array:
    var touched := {}
    var ids: Array = []
    for g in overlaps:
        for name in overlaps[g]:
            touched[String(name)] = true
    for device_id in _device_index:
        if touched.has(String(_device_index[device_id])):
            _device_contacts[device_id] = int(_device_contacts.get(device_id, 0)) + 1
            ids.append(device_id)
    return ids


func _device_snapshot() -> Dictionary:
    var out := {}
    for device_id in _device_index:
        var n = _device_nodes.get(device_id)
        if n == null or not is_instance_valid(n) or not n.is_inside_tree():
            out[device_id] = {"present": false}
            continue
        var p := _pos(n)
        var entry := {
            "present": _alive(n), "x": p.x, "y": p.y, "z": p.z,
            "resolver_evidence": "gb_group+engine_position+runtime_behaviour",
        }
        if n.has_method("observe") and n.get_method_argument_count("observe") == 0:
            var observed = n.call("observe")
            if typeof(observed) == TYPE_DICTIONARY:
                for key in observed:
                    var value = observed[key]
                    if typeof(value) in [TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING]:
                        entry[String(key)] = value
                                                                               
                                                                            
                                                                      
                if typeof(observed.get("progress")) in [TYPE_INT, TYPE_FLOAT]:
                    entry["travel_t"] = clampf(float(observed["progress"]), 0.0, 1.0)
                elif typeof(observed.get("phase")) in [TYPE_INT, TYPE_FLOAT]:
                    entry["travel_t"] = clampf(float(observed["phase"]), 0.0, 1.0)
                                                                               
                                                                             
                                                                               
                                                       
                for event_key in ["opened_count", "launches", "delivered"]:
                    if not observed.has(event_key):
                        continue
                    var counter_key := "%s:%s" % [device_id, event_key]
                    var current := float(observed[event_key])
                    if _device_event_counters.has(counter_key) \
                            and current > float(_device_event_counters[counter_key]):
                        _device_contacts[device_id] = int(_device_contacts.get(device_id, 0)) + 1
                    _device_event_counters[counter_key] = current
        out[device_id] = entry
    return out


func _standing_device(player: Node) -> String:
    if player == null or not player.has_method("standing_on_node"):
        return ""
    var surface = player.call("standing_on_node")
    while surface != null and surface is Node:
        var instance := int((surface as Node).get_instance_id())
        if _device_by_instance.has(instance):
            return String(_device_by_instance[instance])
        surface = (surface as Node).get_parent()
    return ""


                                                                             

func _physics_process(_delta: float) -> void:
    if _finished:
        return
    match _phase:
        "video_start_gate":
            _tick_video_start_gate()
        "boot":
            _tick_boot()
        "settle":
            _tick_settle()
        "setup":
            _tick_setup()
        "run":
            _tick_run()


func _tick_video_start_gate() -> void:
    if not FileAccess.file_exists(_video_start_gate):
        return
    DirAccess.remove_absolute(_video_start_gate)
    get_tree().paused = false
    _phase = "boot"


var _asked_for_scene := false
var _enter_armed := 0


                                                                               
                                                                                
                                                                            
                                                    
func _tick_boot() -> void:
    _apply_plan_file()
    if _phase != "boot":
        return
    _boot += 1
    var scene := _scene_path()
                                                                       
                                                                           
                                                    
    if _requested_scene != "" and not _asked_for_scene and _boot > 90 \
            and scene != "" and scene != _requested_scene:
        var inject = _plan.get("inject", {})
        if typeof(inject) == TYPE_DICTIONARY and inject.has("autoload_state"):
            if _apply_autoload_state_inject(inject["autoload_state"]):
                if not _early_autoload_injected:
                    _injects += 1
                _early_autoload_injected = true
            else:
                _errors.append(
                    "inject.autoload_state could not be applied before requested scene")
                _finish("reference_observation_missing")
                return
        _asked_for_scene = true
        print("gb_route change_scene %s -> %s" % [scene, _requested_scene])
        get_tree().change_scene_to_file(_requested_scene)
        return
    var player := _player()
    if player != null:
        _player_seen = true
        if _requested_scene == "" or scene == _requested_scene or _boot > 300:
            _enter_segment(player)
            return
                                                                           
                                                                            
                                                                         
    if _boot % 30 == 0 and _boot > 60 \
            and (_requested_scene == "" or _asked_for_scene):
        _try_entry()
    if _boot >= int(_plan.get("boot_deadline", 900)):
        if player != null:
            _enter_segment(player)
            return
        _finish("no_player")


func _try_entry() -> void:
    var tree := get_tree()
    if tree == null:
        return
    var buttons: Array = []
    _visible_buttons(tree.root, 0, buttons)
    if buttons.size() > 0:
        var b: BaseButton = buttons[0]
        b.grab_focus()
        b.emit_signal("pressed")
        return
    for a in ENTRY_ACTIONS:
        if InputMap.has_action(a):
            _press_action(a)
            _release_action(a)


func _visible_buttons(node: Node, depth: int, out: Array) -> void:
    if depth > WALK_DEPTH:
        return
    for child in node.get_children():
        if child is BaseButton and (child as BaseButton).is_visible_in_tree() \
                and not (child as BaseButton).disabled:
            out.append(child)
        _visible_buttons(child, depth + 1, out)


func _enter_segment(player: Node) -> void:
    _start_scene = _scene_path()
    _last_scene = _start_scene
    if _whole_game and _scene_is_level(_start_scene):
        _mark_level_visit(_start_scene)
    _had_player = true
    print("gb_route enter_segment scene=%s" % _start_scene)
                                                                  
                                                                        
                                                                          
                                                                             
                         
    _last_pos = _pos(player)
    _phase = "settle"
    _enter_armed = 8 if OS.get_name() == "Windows" else 0
    if _enter_armed == 0:
        _finish_enter_segment()
    else:
        get_tree().paused = true


func _finish_enter_segment() -> void:
    var player := _player()
    print("gb_route indexing devices")
    _build_device_index()
    print("gb_route devices=%d" % _device_index.size())
    for g in _groups():
        _group_totals[g] = _nodes(g).size()
    if player == null:
        return
    var inject = _plan.get("inject", {})
    if typeof(inject) == TYPE_DICTIONARY and inject.has("player"):
        if _apply_player_inject(player, inject["player"]):
            _injects += 1
        else:
            _errors.append("inject.player could not be applied to this player node")
    if typeof(inject) == TYPE_DICTIONARY and inject.has("state"):
        if _apply_state_inject(inject["state"]):
            _injects += 1
        else:
            _errors.append("inject.state could not be applied completely")
    var at_device := String(_plan.get("at_device", ""))
    if at_device != "":
        var target = _device_nodes.get(at_device)
        if target != null and is_instance_valid(target):
            var at := _pos(target)
            var anchor := String(_plan.get("anchor", ""))
            if anchor != "":
                if not target.has_method("observe") or target.get_method_argument_count("observe") != 0:
                    _errors.append(
                        "reference-only observe() missing: start.anchor '%s' on %s is unmeasurable" \
                        % [anchor, at_device])
                    _finish("reference_observation_missing")
                    return
                var observed = target.call("observe")
                if typeof(observed) == TYPE_DICTIONARY and typeof(observed.get(anchor)) == TYPE_VECTOR3:
                    at = observed[anchor]
                else:
                    _errors.append(
                        "reference-only observe().%s missing on %s; semantic placement is unmeasurable" \
                        % [anchor, at_device])
                    _finish("reference_observation_missing")
                    return
            var bounds := _bounds_from_groups()
            var mn = bounds.get("min", {})
            var mx = bounds.get("max", {})
            var offset = _plan.get("offset_norm", {})
            at += Vector3(
                float(offset.get("x", 0.0)) * (float(mx.get("x", 0.0)) - float(mn.get("x", 0.0))),
                float(offset.get("y", 0.0)) * (float(mx.get("y", 0.0)) - float(mn.get("y", 0.0))),
                float(offset.get("z", 0.0)) * (float(mx.get("z", 0.0)) - float(mn.get("z", 0.0))))
            if _apply_player_inject(player, {"x": at.x, "y": at.y, "z": at.z}):
                _injects += 1
            else:
                _errors.append("start.at_device could not place this player")
        else:
            _errors.append(
                "start.at_device '%s' did not resolve from gb groups and runtime evidence" % at_device)
            _finish("reference_observation_missing")
            return
func _tick_setup() -> void:
    _setup_frames += 1
    var player := _player()
    if player == null:
        var scene := _scene_path()
                                                                        
                                                                        
                                                                            
                                                                        
        if _success_scenes.has(scene):
            _errors.append("candidate entered a declared success ending during setup")
            _finish("setup_success_ending")
        elif _failure_scenes.has(scene):
            _errors.append("candidate entered a declared failure ending during setup")
            _finish("setup_failure_ending")
        elif _start_scene != "" and scene != _start_scene:
            _errors.append("candidate left the gameplay scene during setup")
            _finish("setup_scene_changed")
        else:
            _errors.append("player disappeared during setup")
            _finish("setup_failed")
        return
    var overlaps := _overlaps(player)
    _note_contacts(overlaps)
    _advance_ops()


func _finish_setup() -> void:
    _release_all()
                                                                           
                                                                            
                                                                       
    _device_contacts.clear()
                                                                           
                                                                         
                                                
    _setup_ops.clear()
    _ops = _main_ops.duplicate()
    _op_i = -1
    _op_left = 0
    _last_pos = _pos(_player())
                                                                            
                                                                              
                                                                    
    _phase = "run"


func _apply_player_inject(player: Node, spec) -> bool:
    if typeof(spec) != TYPE_DICTIONARY or not spec.has("x") or not spec.has("y"):
        return false
    if player is Node2D:
        (player as Node2D).global_position = Vector2(float(spec["x"]), float(spec["y"]))
        return true
    if player is Node3D:
        (player as Node3D).global_position = Vector3(
            float(spec["x"]), float(spec["y"]), float(spec.get("z", 0.0)))
        return true
    if player is Control:
        (player as Control).global_position = Vector2(float(spec["x"]), float(spec["y"]))
        return true
    return false


func _apply_state_inject(entries) -> bool:
                                                                          
                                                                              
                                                                            
                                                                           
                           
    if typeof(entries) != TYPE_ARRAY or entries.is_empty():
        return false
    var scene := get_tree().current_scene
    if scene == null:
        return false
    var all_ok := true
    for raw in entries:
        if typeof(raw) != TYPE_DICTIONARY:
            all_ok = false
            continue
        var node_path := String(raw.get("node", ""))
        var property := String(raw.get("property", ""))
        var operation := String(raw.get("op", "set"))
        var value = raw.get("value")
        var target := scene.get_node_or_null(NodePath(node_path))
        if target == null or property == "" or typeof(value) not in [
                TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING]:
            all_ok = false
            continue
        var found := false
        for info in target.get_property_list():
            if String(info.get("name", "")) == property:
                found = true
                break
        if not found:
            all_ok = false
            continue
        var next = value
        if operation == "add":
            var current = target.get(property)
            if typeof(current) not in [TYPE_INT, TYPE_FLOAT] \
                    or typeof(value) not in [TYPE_INT, TYPE_FLOAT]:
                all_ok = false
                continue
            next = current + value
        elif operation != "set":
            all_ok = false
            continue
        target.set(property, next)
    return all_ok


func _apply_autoload_state_inject(entries) -> bool:
                                                                         
                                                                             
                                                                    
    if typeof(entries) != TYPE_ARRAY or entries.is_empty():
        return false
    var all_ok := true
    for raw in entries:
        if typeof(raw) != TYPE_DICTIONARY:
            all_ok = false
            continue
        var node_name := String(raw.get("node", ""))
        var property := String(raw.get("property", ""))
        var operation := String(raw.get("op", "set"))
        var value = raw.get("value")
        var target := get_node_or_null("/root/" + node_name)
        if target == null or property == "" or typeof(value) not in [
                TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING]:
            all_ok = false
            continue
        var found := false
        for info in target.get_property_list():
            if String(info.get("name", "")) == property:
                found = true
                break
        if not found:
            all_ok = false
            continue
        var next = value
        if operation == "add":
            var current = target.get(property)
            if typeof(current) not in [TYPE_INT, TYPE_FLOAT] \
                    or typeof(value) not in [TYPE_INT, TYPE_FLOAT]:
                all_ok = false
                continue
            next = current + value
        elif operation != "set":
            all_ok = false
            continue
        target.set(property, next)
    return all_ok


func _tick_settle() -> void:
    if _enter_armed > 0:
        _enter_armed -= 1
        if _enter_armed == 0:
            get_tree().paused = false
            _finish_enter_segment()
        return
    _settle_left -= 1
    if _settle_left <= 0:
                                                                                
                                                                             
                                                                            
        _phase = "setup" if not _setup_ops.is_empty() else "run"


func _tick_run() -> void:
    if _await_agent:
        _poll_agent()
        return

    var scene := _scene_path()
    var player := _player()
    var success_transaction := not _whole_game and bool(_plan.get("continue_after_success", false))
                                                                               
                                                                              
                                               
    if _whole_game and player != null and _scene_is_level(scene):
        _mark_level_visit(scene)
                                                                              
                                                                             
                                                                           
                                                                             
                                                                              
                                                                          
                                                                   
    if scene == "":
        _record_row(null)
        if _rows.size() >= _budget:
            _finish("budget_frames")
        elif _whole_game or success_transaction:
                                                                     
                                                                           
                                                                             
                                                                           
                                                                
                                                                
            _advance_ops()
        return
    if success_transaction and _tick_success_transaction(scene, player):
        return
    if _whole_game and scene != _last_scene:
        var left_scene := _last_scene
        _last_scene = scene
        if _scene_is_level(scene):
            if bool(_plan.get("continue_after_failure", false)) and _whole_game_outcome == "failure":
                _whole_game_outcome = ""
            _mark_level_visit(scene)
            _build_device_index()
            for g in _groups():
                _group_totals[g] = maxi(
                    int(_group_totals.get(g, 0)), _nodes(g).size())
        elif _failure_scenes.has(scene):
                                                                             
                                                                              
                                                                   
            _whole_game_outcome = "failure"
            if bool(_plan.get("continue_after_failure", false)):
                                                                            
                                                                            
                                                                           
                _deaths += 1
                _had_player = false
            else:
                _record_row(player)
                _finish("wrong_ending")
                return
        elif _success_scenes.has(scene):
                                                                           
                                                                             
                                                                             
                                                                          
                                                                         
                                                                           
                                                                          
                                                                             
                                                                            
                                                                        
                                                                          
                                                                      
            if str(_plan.get("success_scope", "declared_ending")) == "all_declared_levels" \
                    and _visited_levels.size() < _required_level_count():
                                                                          
                                                                              
                                                                            
                                                                          
                                                                          
                                                                     
                                                   
                pass
            else:
                _record_row(player)
                if _visited_levels.size() < _required_level_count():
                    _success_before_all_levels = true
                _finish_whole_game_success()
                return
        elif _continuation_scenes.has(scene):
                                                                             
                                                                             
                                                                            
            # scene.  This is a known-success interstitial, not an unknown
                                                                              
                                                                        
            _record_row(player)
        elif _scene_is_level(left_scene) \
                and _visited_levels.size() >= _required_level_count():
                                                                             
                                                                                
                                                                                 
                                                                                           
                                                                              
                                                             
             
                                                                             
                                                                                
                                                                              
                                                                                
                                                                         
                                                                              
                                                          
            _record_row(player)
            if not _ending_vocab:
                _whole_game_outcome = "undeclared"
                _finish("no_ending_vocabulary")
            elif _success_scenes.has(scene):
                _finish_whole_game_success()
            else:
                _whole_game_outcome = "failure" if _failure_scenes.has(scene) else "unknown"
                _finish("wrong_ending")
            return
    if player == null:
        if _had_player and (not _whole_game or _scene_is_level(scene)):
            _deaths += 1
            _had_player = false
        _record_row(null)
                                                                                
                                                                              
                                                                             
                                                                     
        if not _whole_game and _start_scene != "" and scene != _start_scene:
            if _success_scenes.has(scene):
                _finish("goal_reached")
            else:
                _finish("scene_changed")
            return
        if _rows.size() >= _budget:
            _finish("budget_frames")
        elif _whole_game or success_transaction:
                                                                      
                                                                            
            _advance_ops()
        return
    _had_player = true

    if not _whole_game and _start_scene != "" and scene != _start_scene:
        _record_row(player)
        _finish("scene_changed")
        return

    var overlaps := _overlaps(player)
    var contact_ids := _note_contacts(overlaps)
    _record_row(player, overlaps, contact_ids)

    var here := _pos(player)
    if here.distance_to(_last_pos) < 0.01:
        _stall += 1
    _last_pos = here

    if _goal_satisfied(overlaps):
        _goal_row = _rows.size() - 1
        _finish("goal_reached")
        return
    if _rows.size() >= _budget:
        _finish("budget_frames")
        return

    _advance_ops()


func _tick_success_transaction(scene: String, player) -> bool:
                                                                              
    # Only declared success/interstitial scenes continue; failure and unknown
                                                                           
    if scene != _last_scene:
        _last_scene = scene
        if _scene_is_level(scene):
            _whole_game_outcome = ""
            _whole_game_clear = false
            _start_scene = scene
                                                                             
                                                                             
            _build_device_index()
            for g in _groups():
                _group_totals[g] = maxi(
                    int(_group_totals.get(g, 0)), _nodes(g).size())
        elif not _success_scenes.has(scene) and not _continuation_scenes.has(scene):
            _whole_game_clear = false
            _whole_game_outcome = "failure" if _failure_scenes.has(scene) else "unknown"
            _record_row(player)
            _finish("wrong_ending" if _failure_scenes.has(scene) else "scene_changed")
            return true
    if _success_scenes.has(scene) or _continuation_scenes.has(scene):
        _had_player = false
        _record_row(player)
        if _success_scenes.has(scene):
            _whole_game_outcome = "success"
            _whole_game_clear = true
            _rows[_rows.size() - 1]["wgc"] = true
        if _rows.size() >= _budget:
            _finish("budget_frames")
        else:
            _advance_ops()
        return true
    return false


func _finish_whole_game_success() -> void:
    _whole_game_clear = true
    _whole_game_outcome = "success"
    if not _rows.is_empty():
        _rows[_rows.size() - 1]["wgc"] = true
    _goal_row = _rows.size() - 1
    _finish("goal_reached")


func _goal_satisfied(overlaps: Dictionary) -> bool:
    for g in _stop_overlap:
        var hit = overlaps.get(String(g), [])
        if hit is Array and (hit as Array).size() > 0:
            return true
    for g in _stop_collect:
        var total := int(_group_totals.get(String(g), 0))
        if total > 0 and _alive_count(String(g)) == 0:
            return true
    return false


func _alive_count(group: String) -> int:
    var n := 0
    for node in _nodes(group):
        if _alive(node):
            n += 1
    return n


func _advance_ops() -> void:
    if _idle:
        return
    if _op_left > 0:
        _op_left -= 1
        return
                                                                          
                                                                          
                                                                              
                                                                              
                                                                    
    if _op_i + 1 >= _ops.size():
        _release_all()
        if _phase == "setup":
            _finish_setup()
                                                                         
                                                                            
                                                                            
            _advance_ops()
            return
        if _interactive and _turns < _max_turns:
            _request_ops()
            return
        if _tail_left > 0:
            _tail_left -= 1
            return
        _finish("ops_exhausted")
        return
    _op_i += 1
    _steps += 1
    var op = _ops[_op_i]
    if typeof(op) != TYPE_DICTIONARY:
        _errors.append("op %d was not an object" % _op_i)
        return
    var name := String(op.get("op", ""))
    var actions: Array = op.get("actions", [])
    if actions.is_empty() and String(op.get("action", "")) != "":
        actions = [String(op.get("action", ""))]
    var frames := maxi(1, int(op.get("frames", 1)))
    var axis_map = op.get("axes", {})
    if typeof(axis_map) != TYPE_DICTIONARY:
        axis_map = {}
    if name == "state":
        var flush_edges := _edge_flush_pending
        _edge_flush_pending = false
        var desired := {}
        for raw_action in actions:
            var action := String(raw_action)
            if _route_action_ok(action):
                desired[action] = true
            elif not InputMap.has_action(action):
                _errors.append("action '%s' is not in this project's InputMap" % action)
            else:
                _errors.append("action '%s' is outside the frozen dispatch set" % action)
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
            _flush_action_edges()
        _apply_axes(axis_map)
        _op_left = frames - 1
        return
    var flush_edges := _release_all(false)
    match name:
        "hold", "tap":
            for raw_action in actions:
                var action := String(raw_action)
                if _route_action_ok(action):
                    _press_action(action)
                    _held[action] = true
                elif not InputMap.has_action(action):
                    _errors.append("action '%s' is not in this project's InputMap" % action)
                else:
                    _errors.append("action '%s' is outside the frozen dispatch set" % action)
        "release":
            for raw_action in actions:
                var action := String(raw_action)
                if InputMap.has_action(action):
                    _release_action(action)
                    _held.erase(action)
        _:
            pass
                                                                            
                                                                               
                                                                           
                                                                             
    if name == "tap" and frames == 1:
        flush_edges = true
        _edge_flush_pending = true
    if flush_edges:
        _flush_action_edges()
    _apply_axes(axis_map)
    _op_left = frames - 1


func _route_action_ok(action: String) -> bool:
                                                                            
                                                                              
                                                                          
                                                                        
                                     
    if not InputMap.has_action(action):
        return false
    var probe := _probe()
    if probe != null and probe.has_method("is_dispatchable_action"):
        return probe.is_dispatchable_action(action)
    return action.begins_with("gb_")


func _route_axis_ok(axis_id: String) -> bool:
    var probe := _probe()
    if probe != null and probe.has_method("is_dispatchable_axis"):
        return probe.is_dispatchable_axis(axis_id)
    return false


func _set_axis(axis_id: String, value: float) -> void:
    var probe := _probe()
    if probe != null and probe.has_method("set_axis"):
        probe.set_axis(axis_id, value)


func _apply_axes(desired) -> void:
    if typeof(desired) != TYPE_DICTIONARY:
        desired = {}
    for raw_id in _axis_held.keys():
        var axis_id := String(raw_id)
        if not desired.has(axis_id):
            _set_axis(axis_id, 0.0)
            _axis_held.erase(axis_id)
    for raw_id in desired.keys():
        var axis_id := String(raw_id)
        if _route_axis_ok(axis_id):
            var value := float(desired[raw_id])
            _set_axis(axis_id, value)
            _axis_held[axis_id] = value
        else:
            _errors.append("axis '%s' is outside the frozen dispatch set" % axis_id)


func _clear_axes() -> void:
    for raw_id in _axis_held.keys():
        _set_axis(String(raw_id), 0.0)
    _axis_held.clear()


func _release_all(flush_if_required: bool = true) -> bool:
    var flush_edges := _edge_flush_pending
    _edge_flush_pending = false
    for action in _held.keys():
        if InputMap.has_action(String(action)):
            _release_action(String(action))
    _held.clear()
    _clear_axes()
    if flush_if_required and flush_edges:
        _flush_action_edges()
        return false
    return flush_edges


func _flush_action_edges() -> void:
    var probe := _probe()
    if probe != null and probe.has_method("flush_input_events"):
        probe.flush_input_events()


func _press_action(action: String) -> void:
                                                                               
                                                                              
                                                                              
                                                                               
    var probe := _probe()
    if probe != null:
                                                                                
                                                                               
        probe.press(action, false, false)


func _release_action(action: String) -> void:
    var probe := _probe()
    if probe != null:
        probe.release(action, false, false)


                                                                             
  
                                                                               
                                                                           
                                                                         
                    

func _request_ops() -> void:
    if _io_dir == "":
        _finish("ops_exhausted")
        return
    get_tree().paused = true
    _await_agent = true
    _await_frames = 0
    var payload := _observation()
    _write_json("%s/obs_%04d.json" % [_io_dir, _turns], payload)


func _poll_agent() -> void:
    _await_frames += 1
    if _await_frames > _agent_deadline:
        get_tree().paused = false
        _await_agent = false
        _finish("agent_timeout")
        return
    var path := "%s/act_%04d.json" % [_io_dir, _turns]
    if not FileAccess.file_exists(path):
        return
                                                                        
                                                                          
                                                                     
    var parsed = JSON.parse_string(FileAccess.get_file_as_string(path))
    if typeof(parsed) != TYPE_DICTIONARY:
        return
    _turns += 1
    get_tree().paused = false
    _await_agent = false
    if bool(parsed.get("stop", false)):
        _finish(String(parsed.get("stop_reason", "agent_stop")))
        return
    var ops = parsed.get("ops", [])
    if typeof(ops) != TYPE_ARRAY or (ops as Array).is_empty():
        _finish("agent_no_ops")
        return
    for entry in ops:
        _ops.append(entry)
    _op_left = 0


func _observation() -> Dictionary:
    var player := _player()
    var groups := {}
    for g in _groups():
        groups[g] = {"alive": _alive_count(g), "total": int(_group_totals.get(g, 0))}
    var bounds := _bounds_from_groups()
    var origin := {"x": 0.0, "y": 0.0, "z": 0.0}
    var extent := {"x": 1.0, "y": 1.0, "z": 1.0}
    if bounds.has("min") and bounds.has("max"):
        var mn: Dictionary = bounds["min"]
        var mx: Dictionary = bounds["max"]
        origin = {"x": float(mn.get("x", 0.0)), "y": float(mn.get("y", 0.0)), "z": float(mn.get("z", 0.0))}
        extent = {
            "x": maxf(0.001, float(mx.get("x", 0.0)) - float(mn.get("x", 0.0))),
            "y": maxf(0.001, float(mx.get("y", 0.0)) - float(mn.get("y", 0.0))),
            "z": maxf(0.001, float(mx.get("z", 0.0)) - float(mn.get("z", 0.0))),
        }
    var numeric := _numeric_bag()
    var obs := {
        "turn": _turns,
        "frames_used": _rows.size(),
        "frames_budget": _budget,
        "steps": _steps,
        "scene": _scene_path(),
        "whole_game_clear": _whole_game_clear,
        "visited_levels": _visited_levels.keys(),
        "groups": groups,
        "overlaps": _overlaps(player),
        "devices": _device_index.keys(),
        "d": _device_snapshot(),
        "numeric": numeric,
        "origin": origin,
        "extent": extent,
        "standing_on": _standing_device(player),
        "deaths": _deaths,
        "carrying": bool(numeric.get("carrying", 0.0) > 0.0) or bool(numeric.get("cells_carried", 0.0) > 0.0),
        "numeric_source": _numeric_source,
        "camera_yaw": 0.0,
        "camera_yaw_source": "missing",
    }
    if player != null:
        var p := _pos(player)
        var v := _vel(player)
        var on_floor := false
        if player.has_method("is_on_floor"):
            on_floor = bool(player.call("is_on_floor"))
        obs["player"] = {"x": p.x, "y": p.y, "z": p.z,
            "vx": v.x, "vy": v.y, "vz": v.z, "on_floor": on_floor}
        obs["on_floor"] = on_floor
        if "camera_yaw" in player:
            obs["camera_yaw"] = float(player.get("camera_yaw"))
            obs["camera_yaw_source"] = "player.camera_yaw"
        elif player.get("camera_rig") != null:
            var rig = player.get("camera_rig")
            if rig != null and rig.has_method("current_yaw"):
                obs["camera_yaw"] = float(rig.call("current_yaw"))
                obs["camera_yaw_source"] = "camera_rig.current_yaw"
            elif rig != null and "yaw" in rig:
                obs["camera_yaw"] = float(rig.get("yaw"))
                obs["camera_yaw_source"] = "camera_rig.yaw"
        if "carrying" in player:
            obs["carrying"] = bool(player.get("carrying"))
    else:
        obs["player"] = null
        obs["on_floor"] = false
    var goal_nodes := _nodes("gb_goal")
    if goal_nodes.size() > 0 and is_instance_valid(goal_nodes[0]):
        var gp := _pos(goal_nodes[0])
        obs["goal"] = {"x": gp.x, "y": gp.y, "z": gp.z}
    return obs


                                                                             

func _record_row(player, overlaps = null, contact_ids: Array = []) -> void:
    var row := {"f": _rows.size()}
    if player != null:
        var p := _pos(player)
        var v := _vel(player)
        row["px"] = snappedf(p.x, 0.001)
        row["py"] = snappedf(p.y, 0.001)
        row["pz"] = snappedf(p.z, 0.001)
        row["vx"] = snappedf(v.x, 0.001)
        row["vy"] = snappedf(v.y, 0.001)
        row["vz"] = snappedf(v.z, 0.001)
        var fl = player.get("is_on_floor")
        row["fl"] = false
        if player.has_method("is_on_floor"):
            row["fl"] = bool(player.call("is_on_floor"))
    var census := {}
    for g in _groups():
        census[g] = _alive_count(g)
    row["g"] = census
    var numeric_bag := _numeric_bag()
    if not numeric_bag.is_empty():
        row["n"] = numeric_bag
    if overlaps != null:
        var flat := {}
        for g in overlaps:
            if (overlaps[g] as Array).size() > 0:
                flat[g] = overlaps[g]
        row["o"] = flat
    row["d"] = _device_snapshot()
    if _whole_game:
                                                                        
                                                                              
                                                                          
        row["lv"] = _visited_levels.size()
    if not contact_ids.is_empty():
        row["c"] = contact_ids
    var stood := _standing_device(player)
    if stood != "":
        row["so"] = stood
        _device_contacts[stood] = int(_device_contacts.get(stood, 0)) + 1
    if _rows.size() % _sig_interval == 0:
        row["s"] = _signature()
    _rows.append(row)


func _finish(reason: String) -> void:
    if _finished:
        return
    _finished = true
    _release_all()
    if not _rows.is_empty() and not _rows[_rows.size() - 1].has("s"):
        _rows[_rows.size() - 1]["s"] = _signature()
    var report := {
        "ok": true,
        "stop_reason": reason,
        "frames": _rows.size(),
        "physics_frames_at_end": Engine.get_physics_frames(),
        "process_frames_at_end": Engine.get_process_frames(),
        "steps": _steps,
        "turns": _turns,
        "deaths": _deaths,
        "stall_frames": _stall,
        "injects": _injects,
        "out_of_bounds": _out_of_bounds,
        "errors": _errors,
        "player_seen": _player_seen,
        "boot_frames": _boot,
        "requested_scene": _requested_scene,
        "start_scene": _start_scene,
        "scene": _scene_path(),
        "whole_game_clear": _whole_game_clear,
        "whole_game_outcome": _whole_game_outcome,
        "success_before_all_levels": _success_before_all_levels,
        "levels_visited": _visited_levels.size(),
        "levels_declared": _required_level_count(),
        "success_scenes": _success_scenes,
        "failure_scenes": _failure_scenes,
        "group_totals": _group_totals,
        "bounds": _bounds_from_groups(),
        "device_index": _device_index,
        "device_contacts": _device_contacts,
        "setup_frames": _setup_frames,
        "goal_row": _goal_row,
        "ops": _ops,
        "input_ledger": _input_ledger_summary(),
        "overlap_method": "Area exact; otherwise AABB of the first collision shape; 3D fallback is a 1.5-unit distance test",
        "sig_interval": _sig_interval,
        "rows": _rows,
    }
    _write_json(_out_path, report)
    print("GB_ROUTE_DONE reason=%s frames=%d steps=%d" % [reason, _rows.size(), _steps])
    var tree := get_tree()
    if tree != null:
                                                                           
                                                                             
                                                                               
                                                                          
                                                                            
                                                           
        var video_hold_seconds := float(_plan.get("video_hold_seconds", 0.0))
        if video_hold_seconds > 0.0:
            tree.paused = true
            await tree.create_timer(video_hold_seconds, true, false, true).timeout
        tree.paused = false
        tree.quit()


func _input_ledger_summary() -> Dictionary:
    var probe := _probe()
    if probe == null or not probe.has_method("input_ledger"):
        return {"count": 0, "overflow": 0, "tail": []}
    var events: Array = probe.input_ledger()
    var first := maxi(0, events.size() - 16)
    return {
        "count": events.size(),
        "overflow": int(probe.input_ledger_overflow()) \
            if probe.has_method("input_ledger_overflow") else 0,
        "tail": events.slice(first),
    }


func _write_json(path: String, payload: Dictionary) -> void:
    var tmp := path + ".part"
    var f := FileAccess.open(tmp, FileAccess.WRITE)
    if f == null:
        push_error("[gb_route] cannot write " + tmp)
        return
    f.store_string(JSON.stringify(payload))
    f.flush()
    f.close()
    DirAccess.rename_absolute(tmp, path)


func _notification(what: int) -> void:
                                                                            
                                                                         
                                                                            
                                                                           
                                                                        
    if what == NOTIFICATION_WM_CLOSE_REQUEST:
        if not _finished and _phase != "boot":
            _finish("window_closed")
