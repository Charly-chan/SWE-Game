extends Node

                                                                    

const PlaythroughScript: Script = preload("res://gb_playthrough.gd")
const TAIL_FRAMES: int = 60

var _active: bool = false


func _ready() -> void:
	_active = OS.get_environment("GB_DEMO") == "1"
	if not _active:
		return
	process_mode = Node.PROCESS_MODE_ALWAYS
	call_deferred("_run_demo")


func _process(_delta: float) -> void:
	if _active:
		_hide_text_recursive(get_tree().current_scene)


func _run_demo() -> void:
	var playthrough_value: Variant = PlaythroughScript.new()
	if not playthrough_value is Node:
		await _finish({
			"verdict": "inconclusive",
			"success": false,
			"message": "could not instantiate playthrough driver",
			"error": "could not instantiate playthrough driver",
			"detail": {},
		})
		return
	var playthrough: Node = playthrough_value
	add_child(playthrough)
	var result_value: Variant = await playthrough.call(
		"run", OS.get_environment("GB_DEMO_TRACE") == "1"
	)
	var result: Dictionary = result_value if result_value is Dictionary else {
		"verdict": "inconclusive",
		"success": false,
		"message": "playthrough driver returned an invalid result",
		"error": "playthrough driver returned an invalid result",
		"detail": {},
	}
	if String(result.get("verdict", "inconclusive")) == "completed":
		for _frame: int in range(TAIL_FRAMES):
			await get_tree().physics_frame
	await _finish(result)


func _finish(result: Dictionary) -> void:
	for line: Variant in result.get("trace", []):
		print("GB_DEMO_TRACE " + String(line))
	result.erase("trace")
	print("GB_DEMO_RESULT " + JSON.stringify(result))
	await get_tree().process_frame
	get_tree().quit(0 if bool(result.get("success", false)) else 1)


                                                                         
func _hide_text_recursive(node: Node) -> void:
	if node == null:
		return
	if node is CanvasLayer:
		(node as CanvasLayer).visible = false
	elif node is Label:
		(node as Label).visible = false
	for child: Node in node.get_children():
		_hide_text_recursive(child)
