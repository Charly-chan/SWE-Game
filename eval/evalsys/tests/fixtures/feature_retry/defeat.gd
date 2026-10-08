extends Node
func _physics_process(_delta: float) -> void:
    if Input.is_action_just_pressed("gb_jump"):
        RunState.retried = true
        get_tree().change_scene_to_file("res://main.tscn")
