extends Node

                                                                             
                                                                         
                                                                             
                                                               

const DWELL_FRAMES: int = 120
const SETTLE_FRAMES: int = 30


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	call_deferred("_run")


func _run() -> void:
	await get_tree().process_frame
	var shared := get_node_or_null("/root/GBHarnessProbe")
	if shared == null or not shared.has_method("runtime_interface"):
		print("GB_ASSET_ERROR missing evaluator runtime probe")
		get_tree().quit(2)
		return
	if not shared.runtime_interface_ok():
		print("GB_ASSET_ERROR " + shared.runtime_interface_error())
		get_tree().quit(2)
		return
	var runtime: Dictionary = shared.runtime_interface()
	var levels: Array = runtime.get("levels", []) as Array
	if levels.is_empty():
		print("GB_ASSET_ERROR normalized interface declares no level to sample")
		get_tree().quit(2)
		return

	for raw_scene: Variant in levels:
		var scene := str(raw_scene)
		var error := get_tree().change_scene_to_file(scene)
		if error != OK:
			print("GB_ASSET_ERROR could not load normalized level address: " + scene)
			get_tree().quit(2)
			return
		for _frame: int in range(SETTLE_FRAMES):
			await get_tree().physics_frame
		shared.sample_asset_use()
		for _frame: int in range(DWELL_FRAMES):
			await get_tree().physics_frame
		shared.sample_asset_use()

	shared.emit_asset_usage()
	get_tree().quit(0)
