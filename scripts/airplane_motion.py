bl_info = {
    "name": "Airplane Motion Simulator",
    "author": "Grok",
    "version": (1, 0),
    "blender": (4, 3, 0),
    "description": "Simulates realistic airplane movement with size-based physics",
    "category": "Animation"
}


import bpy
import math
from mathutils import Vector
import random
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import FloatProperty, BoolProperty, PointerProperty


class AirplaneMotionProperties(PropertyGroup):
    plane_size: FloatProperty(
        name="Plane Size",
        description="Size of airplane in meters (affects movement scale)",
        default=15.0,
        min=1.0,
        max=100.0
    )
    turbulence_strength: FloatProperty(
        name="Turbulence Strength",
        description="Intensity of random air movements",
        default=0.1,
        min=0.0,
        max=1.0
    )
    rocking_strength: FloatProperty(
        name="Rocking Strength",
        description="Intensity of roll (left-right rocking) motion",
        default=0.5,
        min=0.0,
        max=2.0
    )
    animation_speed: FloatProperty(
        name="Animation Speed",
        description="Speed of the procedural animation",
        default=1.0,
        min=0.1,
        max=5.0
    )
    seed: FloatProperty(
        name="Animation Seed",
        description="Seed value for deterministic animation (same seed = same motion pattern)",
        default=1.0,
        min=0.0,
        max=1000.0
    )
    is_running: BoolProperty(default=False)


class AIRPLANE_OT_Simulator(Operator):
    bl_idname = "airplane.simulator"
    bl_label = "Airplane Motion Simulator"
    bl_options = {'REGISTER'}


    _timer = None
    frame_count = 0
    
    # Physics parameters
    base_lift = 0.0  # No longer counteracting gravity, just floating
    initial_position = Vector((0, 0, 0))
    last_position = Vector((0, 0, 0))
    velocity = Vector((0, 0, 0))
    
    # Procedural animation parameters
    noise_offsets = []
    
    def generate_deterministic_offsets(self, seed_value):
        """Generate deterministic noise offsets based on seed value"""
        # Save the current random state
        state = random.getstate()
        
        # Set a new state based on our seed
        random.seed(seed_value)
        
        # Generate deterministic offsets
        offsets = [
            Vector((random.random() * 100, random.random() * 100, random.random() * 100)) 
            for _ in range(6)
        ]
        
        # Restore the previous random state
        random.setstate(state)
        
        return offsets
    
    @classmethod
    def poll(cls, context):
        return context.active_object is not None


    def modal(self, context, event):
        if event.type == 'TIMER' and context.scene.airplane_motion.is_running:
            obj = context.active_object
            if not obj:
                return {'CANCELLED'}
                
            # Get properties
            props = context.scene.airplane_motion
            size = props.plane_size
            turb = props.turbulence_strength
            rock = props.rocking_strength
            anim_speed = props.animation_speed
            
            # Real-world scale considerations
            # Smaller planes are more affected by turbulence and rocking
            # Larger planes have more inertia and slower responses
            mass_factor = size * size * size / 125  # Cubic relationship for mass
            inertia_factor = math.sqrt(size) / 3.0  # Square root for rotational inertia
            
            # Maximum displacement based on size (larger planes move less relatively)
            max_displacement = size * 0.01 * turb
            
            # Time progression with animation speed
            time = self.frame_count * 0.033 * anim_speed  # ~30 fps
            
            # Generate procedural curves using multiple sine waves with different frequencies
            # This creates more natural, non-repeating patterns
            wind_x = (math.sin(time * 0.42 + self.noise_offsets[0].x) * 0.5 +
                     math.sin(time * 0.71 + self.noise_offsets[0].y) * 0.3 +
                     math.sin(time * 1.13 + self.noise_offsets[0].z) * 0.2)
            
            wind_y = (math.sin(time * 0.53 + self.noise_offsets[1].x) * 0.5 +
                     math.sin(time * 0.97 + self.noise_offsets[1].y) * 0.3 +
                     math.sin(time * 1.29 + self.noise_offsets[1].z) * 0.2)
            
            wind_z = (math.sin(time * 0.37 + self.noise_offsets[2].x) * 0.5 +
                     math.sin(time * 0.83 + self.noise_offsets[2].y) * 0.3 +
                     math.sin(time * 1.07 + self.noise_offsets[2].z) * 0.2)
            
            # Apply turbulence strength and scale
            wind_gust = Vector((
                wind_x * turb * max_displacement,
                wind_y * turb * max_displacement,
                wind_z * turb * max_displacement
            ))
            
            # Calculate acceleration (only turbulence, no significant lift)
            accel = wind_gust / mass_factor
            
            # Update velocity with damping to keep plane near initial position
            self.velocity += accel * 0.033
            
            # Add position restoration force (to keep floating in place)
            position_diff = self.initial_position - self.last_position
            restoration_force = position_diff * (0.1 / inertia_factor)
            self.velocity += restoration_force * 0.033
            
            # Apply damping to prevent excessive movement (larger planes have more momentum)
            damping = 0.95 + (0.03 / inertia_factor)
            if damping > 0.99:
                damping = 0.99  # Cap damping
            self.velocity *= damping
            
            # Generate procedural rotation curves
            # Roll (left-right rocking) with separate control
            roll_factor = (math.sin(time * 0.61 + self.noise_offsets[3].x) * 0.5 +
                          math.sin(time * 1.05 + self.noise_offsets[3].y) * 0.3 +
                          math.sin(time * 1.47 + self.noise_offsets[3].z) * 0.2)
            
            pitch_factor = (math.sin(time * 0.47 + self.noise_offsets[4].x) * 0.5 +
                           math.sin(time * 0.89 + self.noise_offsets[4].y) * 0.3 +
                           math.sin(time * 1.33 + self.noise_offsets[4].z) * 0.2)
            
            yaw_factor = (math.sin(time * 0.39 + self.noise_offsets[5].x) * 0.5 +
                         math.sin(time * 0.77 + self.noise_offsets[5].y) * 0.3 +
                         math.sin(time * 1.21 + self.noise_offsets[5].z) * 0.2)
            
            # Calculate tilt based on velocity and procedural factors
            # Smaller planes rotate more dramatically
            rotation_scale = 1.0 / math.sqrt(size)
            
            # Roll (left-right rocking) with dedicated control parameter
            tilt_x = (-self.velocity.y * 0.4 + roll_factor * 0.6) * rotation_scale * rock  # Roll
            
            # Pitch (nose up-down) affected by turbulence
            tilt_y = (self.velocity.x * 0.6 + pitch_factor * 0.4) * rotation_scale * turb  # Pitch
            
            # Yaw (nose left-right) affected by turbulence
            tilt_z = yaw_factor * 0.15 * rotation_scale * turb  # Yaw
            
            # Apply realistic physics: when an airplane banks (rolls), it tends to descend on the lower wing side
            # This is due to the lift vector being tilted, causing lateral acceleration
            # We'll simulate this by adjusting vertical position based on roll angle
            
            # Calculate bank-induced vertical movement (simplified physics)
            # When a plane banks, it loses some vertical lift component
            bank_angle_rad = tilt_x * 0.1  # Convert to radians
            lift_loss = 1.0 - math.cos(bank_angle_rad)  # Vertical component loss
            
            # Apply a slight downward force proportional to bank angle
            bank_vertical_adjust = -lift_loss * 0.05 * size * rock
            
            # Also apply a slight lateral force in the direction of the bank
            # (planes tend to turn in the direction they're banking)
            bank_lateral_adjust = math.sin(bank_angle_rad) * 0.02 * size * rock
            
            # Update position with banking effects
            banking_adjustment = Vector((bank_lateral_adjust, 0, bank_vertical_adjust))
            new_position = self.last_position + self.velocity * 0.033 + banking_adjustment * 0.033
            
            # Apply to object
            obj.location = new_position
            obj.rotation_euler = (
                tilt_x * 0.1,  # Roll
                tilt_y * 0.1,  # Pitch
                tilt_z * 0.05  # Yaw
            )
            
            # Store current frame data for keyframing if needed
            obj["airplane_motion_frame"] = self.frame_count
            
            self.last_position = new_position.copy()
            self.frame_count += 1
            context.area.tag_redraw()
            return {'PASS_THROUGH'}
            
        if not context.scene.airplane_motion.is_running:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
            return {'FINISHED'}
            
        return {'PASS_THROUGH'}


    def execute(self, context):
        if not context.scene.airplane_motion.is_running:
            # Initialize
            self.frame_count = 0
            self.initial_position = context.active_object.location.copy()
            self.last_position = self.initial_position.copy()
            self.velocity = Vector((0, 0, 0))
            
            # Generate deterministic offsets based on seed
            seed_value = context.scene.airplane_motion.seed
            self.noise_offsets = self.generate_deterministic_offsets(seed_value)
            
            # Start simulation
            wm = context.window_manager
            self._timer = wm.event_timer_add(0.033, window=context.window)
            wm.modal_handler_add(self)
            context.scene.airplane_motion.is_running = True
            return {'RUNNING_MODAL'}
        return {'CANCELLED'}


class AIRPLANE_OT_Stop(Operator):
    bl_idname = "airplane.stop"
    bl_label = "Stop Simulation"
    bl_options = {'REGISTER'}
    
    def execute(self, context):
        context.scene.airplane_motion.is_running = False
        return {'FINISHED'}


class AIRPLANE_OT_BakeAnimation(Operator):
    bl_idname = "airplane.bake_animation"
    bl_label = "Bake Animation"
    bl_options = {'REGISTER', 'UNDO'}
    
    frame_start: bpy.props.IntProperty(
        name="Start Frame",
        description="First frame to bake",
        default=1
    )
    
    frame_end: bpy.props.IntProperty(
        name="End Frame",
        description="Last frame to bake",
        default=250
    )
    
    @classmethod
    def poll(cls, context):
        return context.active_object is not None and not context.scene.airplane_motion.is_running
    
    def execute(self, context):
        obj = context.active_object
        if not obj:
            self.report({'ERROR'}, "No active object")
            return {'CANCELLED'}
        
        # Store original location and rotation
        orig_loc = obj.location.copy()
        orig_rot = obj.rotation_euler.copy()
        
        # Get properties
        props = context.scene.airplane_motion
        
        # Initialize simulation variables
        frame_count = 0
        initial_position = orig_loc.copy()
        last_position = orig_loc.copy()
        velocity = Vector((0, 0, 0))
        
        # Generate deterministic offsets based on seed
        seed_value = int(props.seed)  # Convert to integer for consistent seeding
        
        # Save the current random state
        state = random.getstate()
        
        # Set a new state based on our seed
        random.seed(seed_value)
        
        # Generate deterministic offsets
        noise_offsets = [
            Vector((random.random() * 100, random.random() * 100, random.random() * 100)) 
            for _ in range(6)
        ]
        
        # Restore the previous random state
        random.setstate(state)
        
        # Ensure animation data exists
        if obj.animation_data is None:
            obj.animation_data_create()
        
        # Create new action or use existing
        action_name = f"AirplaneMotion_{obj.name}"
        if action_name not in bpy.data.actions:
            action = bpy.data.actions.new(action_name)
        else:
            action = bpy.data.actions[action_name]
        
        obj.animation_data.action = action
        
        # Clear existing keyframes in range
        if action.fcurves:
            for fcurve in action.fcurves:
                if fcurve.data_path in ["location", "rotation_euler"]:
                    # Create a copy of keyframe points to avoid modification during iteration
                    keyframes_to_remove = []
                    for i, kp in enumerate(fcurve.keyframe_points):
                        if self.frame_start <= kp.co[0] <= self.frame_end:
                            keyframes_to_remove.append(i)
                    
                    # Remove keyframes in reverse order to avoid index shifting
                    for i in sorted(keyframes_to_remove, reverse=True):
                        if i < len(fcurve.keyframe_points):
                            fcurve.keyframe_points.remove(fcurve.keyframe_points[i])
        
        # Bake animation
        for frame in range(self.frame_start, self.frame_end + 1):
            # Update frame count
            frame_count = frame - self.frame_start
            
            # Get properties
            size = props.plane_size
            turb = props.turbulence_strength
            rock = props.rocking_strength
            anim_speed = props.animation_speed
            
            # Real-world scale considerations
            mass_factor = size * size * size / 125  # Cubic relationship for mass
            inertia_factor = math.sqrt(size) / 3.0  # Square root for rotational inertia
            
            # Maximum displacement based on size
            max_displacement = size * 0.01 * turb
            
            # Time progression with animation speed
            time = frame_count * 0.033 * anim_speed  # ~30 fps
            
            # Generate procedural curves using multiple sine waves
            wind_x = (math.sin(time * 0.42 + noise_offsets[0].x) * 0.5 +
                     math.sin(time * 0.71 + noise_offsets[0].y) * 0.3 +
                     math.sin(time * 1.13 + noise_offsets[0].z) * 0.2)
            
            wind_y = (math.sin(time * 0.53 + noise_offsets[1].x) * 0.5 +
                     math.sin(time * 0.97 + noise_offsets[1].y) * 0.3 +
                     math.sin(time * 1.29 + noise_offsets[1].z) * 0.2)
            
            wind_z = (math.sin(time * 0.37 + noise_offsets[2].x) * 0.5 +
                     math.sin(time * 0.83 + noise_offsets[2].y) * 0.3 +
                     math.sin(time * 1.07 + noise_offsets[2].z) * 0.2)
            
            # Apply turbulence strength and scale
            wind_gust = Vector((
                wind_x * turb * max_displacement,
                wind_y * turb * max_displacement,
                wind_z * turb * max_displacement
            ))
            
            # Calculate acceleration
            accel = wind_gust / mass_factor
            
            # Update velocity with damping
            velocity += accel * 0.033
            
            # Add position restoration force
            position_diff = initial_position - last_position
            restoration_force = position_diff * (0.1 / inertia_factor)
            velocity += restoration_force * 0.033
            
            # Apply damping
            damping = 0.95 + (0.03 / inertia_factor)
            if damping > 0.99:
                damping = 0.99  # Cap damping
            velocity *= damping
            
            # Generate procedural rotation curves
            roll_factor = (math.sin(time * 0.61 + noise_offsets[3].x) * 0.5 +
                          math.sin(time * 1.05 + noise_offsets[3].y) * 0.3 +
                          math.sin(time * 1.47 + noise_offsets[3].z) * 0.2)
            
            pitch_factor = (math.sin(time * 0.47 + noise_offsets[4].x) * 0.5 +
                           math.sin(time * 0.89 + noise_offsets[4].y) * 0.3 +
                           math.sin(time * 1.33 + noise_offsets[4].z) * 0.2)
            
            yaw_factor = (math.sin(time * 0.39 + noise_offsets[5].x) * 0.5 +
                         math.sin(time * 0.77 + noise_offsets[5].y) * 0.3 +
                         math.sin(time * 1.21 + noise_offsets[5].z) * 0.2)
            
            # Calculate tilt
            rotation_scale = 1.0 / math.sqrt(size)
            
            tilt_x = (-velocity.y * 0.4 + roll_factor * 0.6) * rotation_scale * rock  # Roll
            tilt_y = (velocity.x * 0.6 + pitch_factor * 0.4) * rotation_scale * turb  # Pitch
            tilt_z = yaw_factor * 0.15 * rotation_scale * turb  # Yaw
            
            # Calculate bank-induced movements
            bank_angle_rad = tilt_x * 0.1
            lift_loss = 1.0 - math.cos(bank_angle_rad)
            bank_vertical_adjust = -lift_loss * 0.05 * size * rock
            bank_lateral_adjust = math.sin(bank_angle_rad) * 0.02 * size * rock
            
            # Update position with banking effects
            banking_adjustment = Vector((bank_lateral_adjust, 0, bank_vertical_adjust))
            new_position = last_position + velocity * 0.033 + banking_adjustment * 0.033
            
            # Set current frame
            context.scene.frame_set(frame)
            
            # Apply the calculated values to the object
            obj.location = new_position
            obj.rotation_euler = (
                tilt_x * 0.1,  # Roll
                tilt_y * 0.1,  # Pitch
                tilt_z * 0.05  # Yaw
            )
            
            # Force update the scene to ensure values are applied
            context.view_layer.update()
            
            # Insert keyframes with proper interpolation
            loc_keyframe = obj.keyframe_insert(data_path="location", frame=frame)
            rot_keyframe = obj.keyframe_insert(data_path="rotation_euler", frame=frame)
            
            # Set keyframe interpolation to Bezier for smoother animation
            for fc in action.fcurves:
                for kf in fc.keyframe_points:
                    if kf.co[0] == frame:
                        kf.interpolation = 'BEZIER'
            
            # Update for next iteration
            last_position = new_position.copy()
            
            # Print debug info every 10 frames
            if frame % 10 == 0:
                self.report({'INFO'}, f"Baking frame {frame}: pos={new_position}, rot={obj.rotation_euler}")
        
        # Reset object to original position after baking
        obj.location = orig_loc
        obj.rotation_euler = orig_rot
        
        # Set the scene back to the start frame
        context.scene.frame_set(self.frame_start)
        
        self.report({'INFO'}, f"Baked airplane motion from frame {self.frame_start} to {self.frame_end}")
        return {'FINISHED'}
    
    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)


class AIRPLANE_PT_Panel(Panel):
    bl_label = "Airplane Motion"
    bl_idname = "AIRPLANE_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Airplane"
    
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        props = scene.airplane_motion
        
        # Properties
        layout.prop(props, "plane_size")
        layout.prop(props, "turbulence_strength")
        layout.prop(props, "rocking_strength")
        layout.prop(props, "animation_speed")
        layout.prop(props, "seed")
        
        # Buttons
        row = layout.row()
        if not props.is_running:
            row.operator("airplane.simulator", text="Start Simulation")
            row.operator("airplane.bake_animation", text="Bake Animation")
        else:
            row.operator("airplane.stop", text="Stop Simulation")


def register():
    bpy.utils.register_class(AirplaneMotionProperties)
    bpy.utils.register_class(AIRPLANE_OT_Simulator)
    bpy.utils.register_class(AIRPLANE_OT_Stop)
    bpy.utils.register_class(AIRPLANE_OT_BakeAnimation)
    bpy.utils.register_class(AIRPLANE_PT_Panel)
    bpy.types.Scene.airplane_motion = PointerProperty(type=AirplaneMotionProperties)


def unregister():
    if hasattr(bpy.types.Scene, "airplane_motion"):
        del bpy.types.Scene.airplane_motion
    bpy.utils.unregister_class(AirplaneMotionProperties)
    bpy.utils.unregister_class(AIRPLANE_OT_Simulator)
    bpy.utils.unregister_class(AIRPLANE_OT_Stop)
    bpy.utils.unregister_class(AIRPLANE_OT_BakeAnimation)
    bpy.utils.unregister_class(AIRPLANE_PT_Panel)


if __name__ == "__main__":
    register()
