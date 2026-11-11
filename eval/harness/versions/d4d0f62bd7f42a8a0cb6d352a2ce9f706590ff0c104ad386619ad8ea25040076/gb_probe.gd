extends Node

const GROUPS: Array[StringName] = [
	&"gb_player",
	&"gb_collectible",
	&"gb_goal",
	&"gb_hazard",
	&"gb_enemy",
	&"gb_checkpoint",
	&"gb_door",
	&"gb_interactive",
]
const ACTIONS: Array[StringName] = [
	&"gb_left",
	&"gb_right",
	&"gb_up",
	&"gb_down",
	&"gb_jump",
	&"gb_action",
	&"gb_pause",
	&"gb_reset",
]
const TRAJECTORY_LIMIT: int = 20000
const EPSILON: float = 0.000001
                                                                                
                                                                
const ASSET_SAMPLE_INTERVAL_FRAMES: int = 15
const ASSET_WALK_DEPTH_LIMIT: int = 6
                                                                               
                                                           
const SETTING_RESOURCE_KEYS: Array[String] = [
	"application/config/icon",
	"application/boot_splash/image",
	"gui/theme/custom",
	"gui/theme/custom_font",
]

const INPUT_LEDGER_LIMIT: int = 20000

var collectible_events: Array[Dictionary] = []
var _injected_events: Array[Dictionary] = []
var _injected_event_overflow: int = 0

var _collectible_states: Dictionary = {}
var _trajectory: Array[Vector3] = []
                                                                             
var _observed_resources: Dictionary = {}
var _asset_sample_counter: int = 0
                                                                             
                                                                                 
                                                                                
                                        
var _asset_sampling_enabled: bool = false
var _runtime_interface: Dictionary = {}
var _runtime_interface_error: String = ""


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_load_runtime_interface()
	_scan_collectibles(false)
	_observe_setting_resources()
	_asset_sampling_enabled = OS.get_environment("GB_ASSET_SAMPLING") == "1"


func _interface_arg(key: String, fallback: String = "") -> String:
	var args := OS.get_cmdline_user_args()
	args.append_array(OS.get_cmdline_args())
	for i in args.size():
		if args[i] == key and i + 1 < args.size():
			return args[i + 1]
		if args[i].begins_with(key + "="):
			return args[i].substr(key.length() + 1)
	return fallback


func _load_runtime_interface() -> void:
	var path := _interface_arg("--gb-interface-file").replace(char(92), "/")
	if path == "" or not FileAccess.file_exists(path):
		_runtime_interface_error = "evaluator runtime interface file is missing"
		return
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
	if not parsed is Dictionary:
		_runtime_interface_error = "evaluator runtime interface file did not parse"
		return
	_runtime_interface = parsed
	var expected_version := int(_interface_arg("--gb-interface-version", "-1"))
	var expected_source := _interface_arg("--gb-interface-source-sha")
	var expected_normalized := _interface_arg("--gb-interface-normalized-sha")
	if int(_runtime_interface.get("interface_version", -1)) != expected_version:
		_runtime_interface_error = "evaluator runtime interface version mismatch"
	elif str(_runtime_interface.get("source_sha256", "")) != expected_source:
		_runtime_interface_error = "evaluator runtime interface source hash mismatch"
	elif str(_runtime_interface.get("normalized_sha256", "")) != expected_normalized:
		_runtime_interface_error = "evaluator runtime interface normalized hash mismatch"


func runtime_interface() -> Dictionary:
	return _runtime_interface.duplicate(true)


func runtime_interface_ok() -> bool:
	return _runtime_interface_error == "" and not _runtime_interface.is_empty()


func runtime_interface_error() -> String:
	return _runtime_interface_error


func _physics_process(_delta: float) -> void:
	_record_player_position()
	_scan_collectibles(true)
	if not _asset_sampling_enabled:
		return
	_asset_sample_counter += 1
	if _asset_sample_counter >= ASSET_SAMPLE_INTERVAL_FRAMES:
		_asset_sample_counter = 0
		sample_asset_use()


func set_passive_utility_mode(enabled: bool) -> void:
	                                                                          
	                                                                       
	                                                                   
	                                                                        
	                                                                          
	                                                                   
	set_physics_process(not enabled)


                          

func get_player() -> Node:
	var tree: SceneTree = get_tree()
	if tree == null:
		return null
	var nodes: Array[Node] = tree.get_nodes_in_group(&"gb_player")
	if nodes.is_empty():
		return null
	var first: Node = nodes[0]
	if not is_instance_valid(first):
		return null
	return first


func player_state() -> Dictionary:
	var tree: SceneTree = get_tree()
	var player_count: int = 0
	if tree != null:
		player_count = tree.get_nodes_in_group(&"gb_player").size()
	var player: Node = get_player()
	if player == null or not is_instance_valid(player) or not player.is_inside_tree():
		return {
			"exists": false,
			"position": {},
			"velocity": {},
			"on_floor": false,
		}

	var position_value: Variant = _node_global_position(player)
	var velocity_value: Variant = null
	var on_floor: bool = false
	if player is CharacterBody2D:
		velocity_value = (player as CharacterBody2D).velocity
		on_floor = (player as CharacterBody2D).is_on_floor()
	elif player is CharacterBody3D:
		velocity_value = (player as CharacterBody3D).velocity
		on_floor = (player as CharacterBody3D).is_on_floor()

	return {
		"exists": player_count == 1,
		"position": _vector_to_dictionary(position_value),
		"velocity": _vector_to_dictionary(velocity_value),
		"on_floor": on_floor,
	}


func scene_path() -> String:
	var tree: SceneTree = get_tree()
	if tree == null or tree.current_scene == null:
		return ""
	return tree.current_scene.scene_file_path


                                                                              
                                                                           
                                                                         
func dominant_plane() -> String:
	return String(world_bounds().get("plane", "xy"))


func world_bounds() -> Dictionary:
	var state: Dictionary = {"has_point": false, "min": Vector3.ZERO, "max": Vector3.ZERO}
	var tree: SceneTree = get_tree()
	if tree == null:
		return _invalid_bounds()

	if tree.current_scene != null:
		_accumulate_scene_geometry(tree.current_scene, state)

	for group: StringName in GROUPS:
		for node: Node in tree.get_nodes_in_group(group):
			if not is_instance_valid(node) or not node.is_inside_tree():
				continue
			var position_value: Variant = _node_global_position(node)
			if position_value is Vector2:
				var position_2d: Vector2 = position_value
				_expand_bounds(state, Vector3(position_2d.x, position_2d.y, 0.0))
			elif position_value is Vector3:
				_expand_bounds(state, position_value)

	if not bool(state["has_point"]):
		return _invalid_bounds()
	var minimum: Vector3 = state["min"]
	var maximum: Vector3 = state["max"]
	var plane: String = _plane_for_extent(maximum - minimum)
	var projected_minimum: Vector2 = _project_to_plane(minimum, plane)
	var projected_maximum: Vector2 = _project_to_plane(maximum, plane)
	return {
		"min": {"x": projected_minimum.x, "y": projected_minimum.y},
		"max": {"x": projected_maximum.x, "y": projected_maximum.y},
		"min3": {"x": minimum.x, "y": minimum.y, "z": minimum.z},
		"max3": {"x": maximum.x, "y": maximum.y, "z": maximum.z},
		"plane": plane,
		"valid": true,
	}


func normalize(pos: Variant) -> Dictionary:
	return _normalize_with_bounds(pos, world_bounds())


                            

func group_names() -> Array[StringName]:
	return GROUPS.duplicate()


func node_is_alive(node: Node) -> bool:
	return _node_is_alive(node)

func group_nodes(group: String) -> Array:
	var tree: SceneTree = get_tree()
	if tree == null:
		return []
	return tree.get_nodes_in_group(StringName(group))


func group_census() -> Dictionary:
	var result: Dictionary = {}
	var bounds: Dictionary = world_bounds()
	for group: StringName in GROUPS:
		var nodes: Array = group_nodes(String(group))
		var alive_count: int = 0
		var positions: Array[Dictionary] = []
		for value: Variant in nodes:
			if not value is Node:
				continue
			var node: Node = value
			if not _node_is_alive(node):
				continue
			alive_count += 1
			var position_value: Variant = _node_global_position(node)
			if position_value != null:
				positions.append(_normalize_with_bounds(position_value, bounds))
		result[String(group)] = {
			"total": nodes.size(),
			"alive": alive_count,
			"positions": positions,
		}
	return result


func group_bounds_3d() -> Dictionary:
	var has_point := false
	var minimum := Vector3.ZERO
	var maximum := Vector3.ZERO
	for group: StringName in GROUPS:
		for value: Variant in group_nodes(String(group)):
			if not value is Node or not _node_is_alive(value):
				continue
			var raw: Variant = _node_global_position(value)
			var point_value: Variant = _as_vector3(raw)
			if point_value == null:
				continue
			var point: Vector3 = point_value
			if not has_point:
				minimum = point
				maximum = point
				has_point = true
			else:
				minimum = minimum.min(point)
				maximum = maximum.max(point)
	if not has_point:
		return {}
	return {
		"min": {"x": minimum.x, "y": minimum.y, "z": minimum.z},
		"max": {"x": maximum.x, "y": maximum.y, "z": maximum.z},
	}


func overlaps(group: String) -> Array:
	var names: Array[String] = []
	var player: Node = get_player()
	if player == null or not is_instance_valid(player) or not player.is_inside_tree():
		return names

	for value: Variant in group_nodes(group):
		if not value is Node:
			continue
		var target: Node = value
		if target == player or not _node_is_alive(target):
			continue
		var overlapping: bool = false
		if target is Area2D:
			overlapping = _area_2d_contains_player(target as Area2D, player)
		elif target is Area3D:
			overlapping = _area_3d_contains_player(target as Area3D, player)
		else:
			overlapping = _collision_bounds_overlap(player, target)
		if overlapping:
			names.append(String(target.name))
	return names


                             

func press(action: String, include_mapped_keys: bool = true, flush: bool = true) -> void:
	_send_action(action, true, include_mapped_keys, flush)


func release(action: String, include_mapped_keys: bool = true, flush: bool = true) -> void:
	_send_action(action, false, include_mapped_keys, flush)


func flush_input_events() -> void:
	Input.flush_buffered_events()


func tap(action: String, frames: int) -> void:
	if not _is_fixed_action(action):
		return
	press(action)
	for _frame: int in range(maxi(frames, 0)):
		await get_tree().physics_frame
	release(action)


func hold_frames(action: String, frames: int) -> void:
	await tap(action, frames)


                       

func teleport(pos: Variant) -> bool:
	var player: Node = get_player()
	if player == null or not is_instance_valid(player) or not player.is_inside_tree():
		return false
	if player is Node2D:
		var position_2d: Variant = _as_vector2(pos)
		if position_2d == null:
			return false
		(player as Node2D).global_position = position_2d
		return true
	if player is Control:
		var control_position: Variant = _as_vector2(pos)
		if control_position == null:
			return false
		(player as Control).global_position = control_position
		return true
	if player is Node3D:
		var position_3d: Variant = _as_vector3(pos, (player as Node3D).global_position.z)
		if position_3d == null:
			return false
		(player as Node3D).global_position = position_3d
		return true
	return false


func snapshot() -> Dictionary:
	var frame: int = Engine.get_physics_frames()
	var ticks_per_second: float = float(Engine.physics_ticks_per_second)
	var elapsed: float = 0.0 if ticks_per_second <= 0.0 else float(frame) / ticks_per_second
	return {
		"frame": frame,
		"elapsed": elapsed,
		"player": player_state(),
		"scene": {"path": scene_path()},
		"world": {"bounds": world_bounds()},
		"groups": group_census(),
		"collectible_events": collectible_events.duplicate(true),
		"trajectory": trajectory_stats(),
	}


func emit_snapshot() -> void:
	print("GB_SNAPSHOT " + JSON.stringify(snapshot()))


                                 
 
                                                                              
                                                                         
                                                                                
                                                                                 
                                                                             
                                                                         

func sample_asset_use() -> void:
	var tree: SceneTree = get_tree()
	if tree == null:
		return
	var frame: int = Engine.get_physics_frames()
	var roots: Array[Node] = []
	if tree.current_scene != null and is_instance_valid(tree.current_scene):
		roots.append(tree.current_scene)
	if tree.root != null:
		roots.append(tree.root)
	for root: Node in roots:
		_walk_node_resources(root, frame)


func observed_assets() -> Dictionary:
	var by_basename: Dictionary = {}
	for path: String in _observed_resources:
		var record: Dictionary = _observed_resources[path]
		var basename: String = String(record.get("basename", ""))
		if basename.is_empty():
			continue
		if not by_basename.has(basename):
			by_basename[basename] = []
		var entries: Array = by_basename[basename]
		entries.append({
			"resource_path": path,
			"first_frame": int(record.get("first_frame", -1)),
			"source": String(record.get("source", "")),
		})
		by_basename[basename] = entries
	return {
		"observed_basenames": by_basename,
		"observed_resource_paths": _observed_resources.keys(),
		"frames_observed": Engine.get_physics_frames(),
	}


func emit_asset_usage() -> void:
	sample_asset_use()
	print("GB_ASSET_USAGE " + JSON.stringify(observed_assets()))


func _observe_setting_resources() -> void:
	for key: String in SETTING_RESOURCE_KEYS:
		var value: Variant = ProjectSettings.get_setting(key, "")
		if value is String and String(value).begins_with("res://"):
			_record_resource_path(String(value), 0, "project_setting:" + key)


func _walk_node_resources(node: Node, frame: int) -> void:
	if node == null or not is_instance_valid(node):
		return
	if node == self:
		return
	for property: Dictionary in node.get_property_list():
		if int(property.get("type", TYPE_NIL)) != TYPE_OBJECT:
			continue
		var usage: int = int(property.get("usage", 0))
		if usage & PROPERTY_USAGE_STORAGE == 0 and usage & PROPERTY_USAGE_EDITOR == 0:
			continue
		var value: Variant = node.get(StringName(property.get("name", "")))
		if value is Resource:
			_walk_resource(value as Resource, frame, String(node.name), 0)
	for child: Node in node.get_children():
		_walk_node_resources(child, frame)


func _walk_resource(resource: Resource, frame: int, source: String, depth: int) -> void:
	if resource == null or depth > ASSET_WALK_DEPTH_LIMIT:
		return
	if not resource.resource_path.is_empty():
		_record_resource_path(resource.resource_path, frame, source)
	                                                                            
	                                                               
	for property: Dictionary in resource.get_property_list():
		if int(property.get("type", TYPE_NIL)) != TYPE_OBJECT:
			continue
		var usage: int = int(property.get("usage", 0))
		if usage & PROPERTY_USAGE_STORAGE == 0 and usage & PROPERTY_USAGE_EDITOR == 0:
			continue
		var value: Variant = resource.get(StringName(property.get("name", "")))
		if value is Resource:
			_walk_resource(value as Resource, frame, source, depth + 1)
	if resource is SpriteFrames:
		var frames: SpriteFrames = resource as SpriteFrames
		for animation: StringName in frames.get_animation_names():
			for index: int in range(frames.get_frame_count(animation)):
				var texture: Texture2D = frames.get_frame_texture(animation, index)
				if texture != null:
					_walk_resource(texture, frame, source, depth + 1)
	elif resource is TileSet:
		var tile_set: TileSet = resource as TileSet
		for source_index: int in range(tile_set.get_source_count()):
			var tile_source: TileSetSource = tile_set.get_source(
				tile_set.get_source_id(source_index)
			)
			if tile_source != null:
				_walk_resource(tile_source, frame, source, depth + 1)


func _record_resource_path(path: String, frame: int, source: String) -> void:
	if path.is_empty() or not path.begins_with("res://"):
		return
	                                                                             
	                                   
	var file_path: String = path
	var separator: int = file_path.find("::")
	if separator >= 0:
		file_path = file_path.substr(0, separator)
	if file_path.is_empty() or _observed_resources.has(file_path):
		return
	_observed_resources[file_path] = {
		"basename": file_path.get_file(),
		"first_frame": frame,
		"source": source,
	}


                                                

func trajectory_stats() -> Dictionary:
	var bounds: Dictionary = world_bounds()
	var plane: String = String(bounds.get("plane", "xy"))
	var sample_count: int = _trajectory.size()
	var total_length: float = 0.0
	var net_displacement: float = 0.0
	var span_x: float = 0.0
	var span_y: float = 0.0
	if sample_count > 0:
		                                                                      
		                                                                 
		var minimum: Vector2 = _project_to_plane(_trajectory[0], plane)
		var maximum: Vector2 = minimum
		for index: int in range(1, sample_count):
			var previous: Vector3 = _trajectory[index - 1]
			var current: Vector3 = _trajectory[index]
			total_length += previous.distance_to(current)
			var projected: Vector2 = _project_to_plane(current, plane)
			minimum.x = minf(minimum.x, projected.x)
			minimum.y = minf(minimum.y, projected.y)
			maximum.x = maxf(maximum.x, projected.x)
			maximum.y = maxf(maximum.y, projected.y)
		net_displacement = _trajectory[0].distance_to(_trajectory[sample_count - 1])
		span_x = maximum.x - minimum.x
		span_y = maximum.y - minimum.y

	var width: float = 0.0
	var height: float = 0.0
	if bool(bounds.get("valid", false)):
		var minimum_bound: Dictionary = bounds["min"]
		var maximum_bound: Dictionary = bounds["max"]
		width = float(maximum_bound["x"]) - float(minimum_bound["x"])
		height = float(maximum_bound["y"]) - float(minimum_bound["y"])
	return {
		"total_path_length": total_length,
		"net_displacement": net_displacement,
		"span_x_ratio": 0.0 if width <= EPSILON else span_x / width,
		"span_y_ratio": 0.0 if height <= EPSILON else span_y / height,
		"samples": sample_count,
	}


func reset_trajectory() -> void:
	_trajectory.clear()


func collectible_events_since(frame: int) -> Array:
	var result: Array[Dictionary] = []
	for event: Dictionary in collectible_events:
		if int(event.get("frame", -1)) >= frame:
			result.append(event.duplicate(true))
	return result


func _record_player_position() -> void:
	var player: Node = get_player()
	if player == null or not is_instance_valid(player) or not player.is_inside_tree():
		return
	var position_value: Variant = _node_global_position(player)
	if position_value is Vector2:
		var point_2d: Vector2 = position_value
		_trajectory.append(Vector3(point_2d.x, point_2d.y, 0.0))
	elif position_value is Vector3:
		_trajectory.append(position_value)
	if _trajectory.size() > TRAJECTORY_LIMIT:
		_trajectory.pop_front()


func _scan_collectibles(emit_events: bool) -> void:
	var frame: int = Engine.get_physics_frames()
	var seen: Dictionary = {}
	for value: Variant in group_nodes("gb_collectible"):
		if not value is Node:
			continue
		var node: Node = value
		if not is_instance_valid(node):
			continue
		var instance_id: int = node.get_instance_id()
		seen[instance_id] = true
		var alive: bool = _node_is_alive(node)
		if not _collectible_states.has(instance_id):
			_collectible_states[instance_id] = {
				"reference": weakref(node),
				"name": String(node.name),
				"alive": alive,
			}
			continue
		_update_collectible_state(instance_id, alive, frame, emit_events)

	var tracked_ids: Array = _collectible_states.keys()
	for value: Variant in tracked_ids:
		var instance_id: int = int(value)
		if seen.has(instance_id):
			continue
		var state: Dictionary = _collectible_states[instance_id]
		var reference: WeakRef = state["reference"]
		var referenced: Variant = reference.get_ref()
		var alive: bool = false
		if referenced is Node and is_instance_valid(referenced):
			alive = _node_is_alive(referenced)
		_update_collectible_state(instance_id, alive, frame, emit_events)
		if referenced == null:
			_collectible_states.erase(instance_id)


func _update_collectible_state(instance_id: int, alive: bool, frame: int, emit_events: bool) -> void:
	if not _collectible_states.has(instance_id):
		return
	var state: Dictionary = _collectible_states[instance_id]
	var previous: bool = bool(state.get("alive", false))
	if previous != alive and emit_events:
		collectible_events.append({
			"frame": frame,
			"name": String(state.get("name", "")),
			"from_alive": previous,
			"to_alive": alive,
		})
	state["alive"] = alive
	_collectible_states[instance_id] = state


                          

func _accumulate_scene_geometry(node: Node, state: Dictionary) -> void:
	if node is TileMap:
		_accumulate_tile_map(node as TileMap, state)
	elif node is TileMapLayer:
		_accumulate_tile_map_layer(node as TileMapLayer, state)
	elif node is GridMap:
		_accumulate_grid_map(node as GridMap, state)
	for child: Node in node.get_children():
		_accumulate_scene_geometry(child, state)


                                                                               
                                                                   
func _accumulate_tile_map_layer(layer: TileMapLayer, state: Dictionary) -> void:
	if layer.tile_set == null:
		return
	var used: Rect2i = layer.get_used_rect()
	if used.size.x <= 0 or used.size.y <= 0:
		return
	var tile_size: Vector2 = Vector2(layer.tile_set.tile_size)
	var local_minimum: Vector2 = Vector2(used.position) * tile_size
	var local_maximum: Vector2 = Vector2(used.end) * tile_size
	var corners: Array[Vector2] = [
		local_minimum,
		Vector2(local_maximum.x, local_minimum.y),
		local_maximum,
		Vector2(local_minimum.x, local_maximum.y),
	]
	for corner: Vector2 in corners:
		var global_corner: Vector2 = layer.to_global(corner)
		_expand_bounds(state, Vector3(global_corner.x, global_corner.y, 0.0))


func _accumulate_tile_map(tile_map: TileMap, state: Dictionary) -> void:
	if tile_map.tile_set == null:
		return
	var used: Rect2i = tile_map.get_used_rect()
	if used.size.x <= 0 or used.size.y <= 0:
		return
	var tile_size: Vector2 = Vector2(tile_map.tile_set.tile_size)
	var local_minimum: Vector2 = Vector2(used.position) * tile_size
	var local_maximum: Vector2 = Vector2(used.end) * tile_size
	var corners: Array[Vector2] = [
		local_minimum,
		Vector2(local_maximum.x, local_minimum.y),
		local_maximum,
		Vector2(local_minimum.x, local_maximum.y),
	]
	for corner: Vector2 in corners:
		var global_corner: Vector2 = tile_map.to_global(corner)
		_expand_bounds(state, Vector3(global_corner.x, global_corner.y, 0.0))


func _accumulate_grid_map(grid_map: GridMap, state: Dictionary) -> void:
	var cells: Array[Vector3i] = grid_map.get_used_cells()
	if cells.is_empty():
		return
	var half_size: Vector3 = grid_map.cell_size * 0.5
	for cell: Vector3i in cells:
		var center: Vector3 = grid_map.map_to_local(cell)
		for x_sign: float in [-1.0, 1.0]:
			for y_sign: float in [-1.0, 1.0]:
				for z_sign: float in [-1.0, 1.0]:
					var local_corner := center + Vector3(
						half_size.x * x_sign,
						half_size.y * y_sign,
						half_size.z * z_sign
					)
					_expand_bounds(state, grid_map.to_global(local_corner))


func _expand_bounds(state: Dictionary, point: Vector3) -> void:
	if not bool(state["has_point"]):
		state["has_point"] = true
		state["min"] = point
		state["max"] = point
		return
	var minimum: Vector3 = state["min"]
	var maximum: Vector3 = state["max"]
	state["min"] = Vector3(
		minf(minimum.x, point.x), minf(minimum.y, point.y), minf(minimum.z, point.z)
	)
	state["max"] = Vector3(
		maxf(maximum.x, point.x), maxf(maximum.y, point.y), maxf(maximum.z, point.z)
	)


func _plane_for_extent(extent: Vector3) -> String:
	                                                                            
	if extent.z > extent.x and extent.z > extent.y:
		return "zy" if extent.y > extent.x else "xz"
	if extent.z > extent.y:
		return "xz"
	return "xy"


func _project_to_plane(point: Vector3, plane: String) -> Vector2:
	match plane:
		"xz":
			return Vector2(point.x, point.z)
		"zy":
			return Vector2(point.z, point.y)
		_:
			return Vector2(point.x, point.y)


func _normalize_with_bounds(pos: Variant, bounds: Dictionary) -> Dictionary:
	if not bool(bounds.get("valid", false)):
		return {"valid": false}
	var point_value: Variant = _as_vector3(pos)
	if point_value == null:
		return {"valid": false}
	var point_3d: Vector3 = point_value
	var point: Vector2 = _project_to_plane(point_3d, String(bounds.get("plane", "xy")))
	var minimum: Dictionary = bounds["min"]
	var maximum: Dictionary = bounds["max"]
	var width: float = float(maximum["x"]) - float(minimum["x"])
	var height: float = float(maximum["y"]) - float(minimum["y"])
	if width <= EPSILON or height <= EPSILON:
		return {"valid": false}
	return {
		"x": clampf((point.x - float(minimum["x"])) / width, 0.0, 1.0),
		"y": clampf((point.y - float(minimum["y"])) / height, 0.0, 1.0),
		"valid": true,
	}


func _area_2d_contains_player(area: Area2D, player: Node) -> bool:
	for body: Node2D in area.get_overlapping_bodies():
		if body == player:
			return true
	for other_area: Area2D in area.get_overlapping_areas():
		if other_area == player:
			return true
	return false


func _area_3d_contains_player(area: Area3D, player: Node) -> bool:
	for body: Node3D in area.get_overlapping_bodies():
		if body == player:
			return true
	for other_area: Area3D in area.get_overlapping_areas():
		if other_area == player:
			return true
	return false


func _collision_bounds_overlap(first: Node, second: Node) -> bool:
	var first_rects: Array[Rect2] = _collision_rects_2d(first)
	var second_rects: Array[Rect2] = _collision_rects_2d(second)
	for first_rect: Rect2 in first_rects:
		for second_rect: Rect2 in second_rects:
			if first_rect.intersects(second_rect, true):
				return true

	var first_boxes: Array[AABB] = _collision_boxes_3d(first)
	var second_boxes: Array[AABB] = _collision_boxes_3d(second)
	for first_box: AABB in first_boxes:
		for second_box: AABB in second_boxes:
			if first_box.intersects(second_box):
				return true
	return false


func _collision_rects_2d(root: Node) -> Array[Rect2]:
	var result: Array[Rect2] = []
	var objects: Array[CollisionObject2D] = []
	_collect_collision_objects_2d(root, objects)
	for object: CollisionObject2D in objects:
		for owner_id: int in object.get_shape_owners():
			var owner_transform: Transform2D = object.shape_owner_get_transform(owner_id)
			var world_transform: Transform2D = object.global_transform * owner_transform
			for shape_index: int in range(object.shape_owner_get_shape_count(owner_id)):
				var shape: Shape2D = object.shape_owner_get_shape(owner_id, shape_index)
				if shape == null:
					continue
				result.append(_transformed_rect(shape.get_rect(), world_transform))
	return result


func _collision_boxes_3d(root: Node) -> Array[AABB]:
	var result: Array[AABB] = []
	var objects: Array[CollisionObject3D] = []
	_collect_collision_objects_3d(root, objects)
	for object: CollisionObject3D in objects:
		for owner_id: int in object.get_shape_owners():
			var owner_transform: Transform3D = object.shape_owner_get_transform(owner_id)
			var world_transform: Transform3D = object.global_transform * owner_transform
			for shape_index: int in range(object.shape_owner_get_shape_count(owner_id)):
				var shape: Shape3D = object.shape_owner_get_shape(owner_id, shape_index)
				if shape == null:
					continue
				var mesh: ArrayMesh = shape.get_debug_mesh()
				if mesh == null:
					continue
				result.append(_transformed_aabb(mesh.get_aabb(), world_transform))
	return result


func _collect_collision_objects_2d(node: Node, result: Array[CollisionObject2D]) -> void:
	if node is CollisionObject2D:
		result.append(node as CollisionObject2D)
	for child: Node in node.get_children():
		_collect_collision_objects_2d(child, result)


func _collect_collision_objects_3d(node: Node, result: Array[CollisionObject3D]) -> void:
	if node is CollisionObject3D:
		result.append(node as CollisionObject3D)
	for child: Node in node.get_children():
		_collect_collision_objects_3d(child, result)


func _transformed_rect(rect: Rect2, transform: Transform2D) -> Rect2:
	var corners: Array[Vector2] = [
		transform * rect.position,
		transform * Vector2(rect.end.x, rect.position.y),
		transform * rect.end,
		transform * Vector2(rect.position.x, rect.end.y),
	]
	var result := Rect2(corners[0], Vector2.ZERO)
	for index: int in range(1, corners.size()):
		result = result.expand(corners[index])
	return result


func _transformed_aabb(box: AABB, transform: Transform3D) -> AABB:
	var first: Vector3 = transform * box.position
	var result := AABB(first, Vector3.ZERO)
	for x_offset: float in [0.0, box.size.x]:
		for y_offset: float in [0.0, box.size.y]:
			for z_offset: float in [0.0, box.size.z]:
				var corner := transform * (box.position + Vector3(x_offset, y_offset, z_offset))
				result = result.expand(corner)
	return result


                       

func _node_is_alive(node: Node) -> bool:
	if node == null or not is_instance_valid(node) or not node.is_inside_tree():
		return false
	                                                                         
	                                                                        
	                                                                      
	                                     
	var visible: Variant = node.get(&"visible")
	if typeof(visible) == TYPE_BOOL:
		return bool(visible)
	return true


func _has_property(object: Object, property_name: StringName) -> bool:
	for property: Dictionary in object.get_property_list():
		if StringName(property.get("name", "")) == property_name:
			return true
	return false


func _node_global_position(node: Node) -> Variant:
	if node is Node2D:
		return (node as Node2D).global_position
	if node is Control:
		return (node as Control).global_position
	if node is Node3D:
		return (node as Node3D).global_position
	return null


func _vector_to_dictionary(value: Variant) -> Dictionary:
	if value is Vector2:
		var vector_2d: Vector2 = value
		return {"x": vector_2d.x, "y": vector_2d.y}
	if value is Vector3:
		var vector_3d: Vector3 = value
		return {"x": vector_3d.x, "y": vector_3d.y, "z": vector_3d.z}
	return {}


func _as_vector2(value: Variant) -> Variant:
	if value is Vector2:
		return value
	if value is Vector3:
		var vector_3d: Vector3 = value
		return Vector2(vector_3d.x, vector_3d.y)
	if value is Dictionary and value.has("x") and value.has("y"):
		return Vector2(float(value["x"]), float(value["y"]))
	return null


func _as_vector3(value: Variant, default_z: float = 0.0) -> Variant:
	if value is Vector3:
		return value
	if value is Vector2:
		var vector_2d: Vector2 = value
		return Vector3(vector_2d.x, vector_2d.y, default_z)
	if value is Dictionary and value.has("x") and value.has("y"):
		return Vector3(
			float(value["x"]),
			float(value["y"]),
			float(value.get("z", default_z))
		)
	return null


func _invalid_bounds() -> Dictionary:
	return {
		"min": {"x": 0.0, "y": 0.0},
		"max": {"x": 0.0, "y": 0.0},
		"min3": {"x": 0.0, "y": 0.0, "z": 0.0},
		"max3": {"x": 0.0, "y": 0.0, "z": 0.0},
		"plane": "xy",
		"valid": false,
	}


func _is_fixed_action(action: String) -> bool:
	return ACTIONS.has(StringName(action))


func _send_action(
		action: String,
		pressed: bool,
		include_mapped_keys: bool = true,
		flush: bool = true) -> void:
	if not _is_fixed_action(action):
		return
	if pressed:
		Input.action_press(action)
	else:
		Input.action_release(action)
	var action_event := InputEventAction.new()
	action_event.action = StringName(action)
	action_event.pressed = pressed
	action_event.strength = 1.0 if pressed else 0.0
	Input.parse_input_event(action_event)
	_record_injected_event("InputEventAction", action, pressed, 0)

	if not include_mapped_keys or not InputMap.has_action(action):
		if flush:
			Input.flush_buffered_events()
		return
	for mapped_event: InputEvent in InputMap.action_get_events(action):
		if not mapped_event is InputEventKey:
			continue
		var mapped_key: InputEventKey = mapped_event as InputEventKey
		var keycode: Key = mapped_key.keycode
		var physical_keycode: Key = mapped_key.physical_keycode
		if keycode == 0:
			keycode = physical_keycode
		if physical_keycode == 0:
			physical_keycode = keycode
		var key_event := InputEventKey.new()
		key_event.keycode = keycode
		key_event.physical_keycode = physical_keycode
		key_event.pressed = pressed
		Input.parse_input_event(key_event)
		_record_injected_event("InputEventKey", action, pressed, int(physical_keycode))
	if flush:
		Input.flush_buffered_events()


                                                               
 
                                                                              
                                                                              
                                                        

func input_ledger() -> Array[Dictionary]:
	return _injected_events.duplicate(true)


func reset_input_ledger() -> void:
	_injected_events.clear()
	_injected_event_overflow = 0


func input_ledger_overflow() -> int:
	return _injected_event_overflow


func _record_injected_event(event_type: String, action: String, pressed: bool, physical_keycode: int) -> void:
	if _injected_events.size() >= INPUT_LEDGER_LIMIT:
		_injected_event_overflow += 1
		return
	_injected_events.append({
		"frame": Engine.get_physics_frames(),
		"process_frame": Engine.get_process_frames(),
		"event": event_type,
		"action": action,
		"pressed": pressed,
		"physical_keycode": physical_keycode,
	})
