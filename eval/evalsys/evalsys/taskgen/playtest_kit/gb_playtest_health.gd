extends Node

                                                                          
                                                                               
                                                                               
                                                                             
                                                                            
                                                                          
                                                                                
 
                                                                            
                                                                        
                                                                            
# pt_tape.py turns those into `health_decreased=yes|no|unresolved`.
 
                                                                         
                                                                       

const MANIFEST_PATH := "res://gb_levels.json"
const MAX_LINES := 200
const GROUPS := ["gb_player", "gb_collectible", "gb_goal", "gb_hazard", "gb_enemy",
	"gb_checkpoint", "gb_door", "gb_interactive"]
const HOLDERS := ["run", "state", "run_state", "game"]

var _property := ""
var _frame := 0
var _readable := false
var _last := 0.0
var _lines := 0
var _truncated := false


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_property = _declared_health_property()
	if _property == "":
		print("PLAYTEST_HEALTH not declared: gb_levels.json has no numeric.health entry")


func _declared_health_property() -> String:
	if not FileAccess.file_exists(MANIFEST_PATH):
		return ""
	var parsed = JSON.parse_string(FileAccess.get_file_as_string(MANIFEST_PATH))
	if typeof(parsed) != TYPE_DICTIONARY:
		return ""
	var numeric = parsed.get("numeric", {})
	if typeof(numeric) != TYPE_DICTIONARY:
		return ""
	var value = numeric.get("health", "")
	return String(value).strip_edges() if typeof(value) == TYPE_STRING else ""


func _physics_process(_delta: float) -> void:
	if _property == "":
		return
	_frame += 1
	var found := [false]
	var value := _resolve(found)
	if not found[0]:
		return
	if not _readable or value != _last:
		_readable = true
		_last = value
		_emit(value)


func _emit(value: float) -> void:
	if _lines >= MAX_LINES:
		if not _truncated:
			_truncated = true
			print("PLAYTEST_HEALTH further changes not printed (%d lines)" % MAX_LINES)
		return
	_lines += 1
	print("PLAYTEST_HEALTH frame=%d value=%s" % [_frame, _fmt(value)])


func _exit_tree() -> void:
	if _property == "":
		return
	print("PLAYTEST_HEALTH_END frame=%d value=%s readable=%s" % [
		_frame, _fmt(_last) if _readable else "-", "yes" if _readable else "no"])


func _fmt(value: float) -> String:
	if value == floor(value) and absf(value) < 1e12:
		return str(int(value))
	return "%.3f" % value


                                                                     
func _resolve(found: Array) -> float:
	var total := 0.0
	var seen := {}
	var tree := get_tree()
	if tree == null:
		return 0.0
	var sources: Array = []
	if tree.root != null:
		for child in tree.root.get_children():
			sources.append(child)
	if tree.current_scene != null:
		sources.append(tree.current_scene)
	for group in GROUPS:
		for node in tree.get_nodes_in_group(group):
			sources.append(node)
	for source in sources:
		if source == self or not is_instance_valid(source):
			continue
		var sid: int = source.get_instance_id()
		if seen.has(sid):
			continue
		seen[sid] = true
		total += _absorb(source, found)
		for holder in HOLDERS:
			var bag = source.get(holder)
			if typeof(bag) == TYPE_OBJECT and bag != null and not (bag is Node):
				var bid: int = bag.get_instance_id()
				if seen.has(bid):
					continue
				seen[bid] = true
				total += _absorb(bag, found)
	return total


func _absorb(obj, found: Array) -> float:
	if obj == null:
		return 0.0
	var value = obj.get(_property)
	if typeof(value) == TYPE_INT or typeof(value) == TYPE_FLOAT:
		found[0] = true
		return float(value)
	return 0.0
