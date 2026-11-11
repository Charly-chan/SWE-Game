extends SceneTree


func _initialize() -> void:
	print("GB_DISPLAY_SERVER=", DisplayServer.get_name())
	print("GB_RENDER_DRIVER=", str(ProjectSettings.get_setting("rendering/renderer/rendering_method")))
	quit()
