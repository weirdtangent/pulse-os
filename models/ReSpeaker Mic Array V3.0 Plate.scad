// ReSpeaker Mic Array v3.0 — inset plate, rotatable holes & feet,
// with deep base-mount recesses fully inside a thicker plate.

// ====== MAIN DIMENSIONS ======
plate_outer_d    = 76;      // your OD
plate_thickness  = 6.0;     // go to 6.0 if you want bigger recesses

// PCB recess pocket
pocket_d         = 72;      // ~board OD + 0.3–0.6
pocket_depth     = 4;     // how far the PCB seats down

// 3-hole board pattern (120° apart)
bolt_circle_d    = 47;
hole_clearance   = 2.8;     // 2.8 M2.5, 3.2 M3
cbore_d          = 5.2;     // board-screw counterbore (0 to disable)
cbore_depth      = 1.0;

vent_circle_d    = 42;
vent_holes       = 7;

// Independent rotations
mount_offset_deg = 60;   // 3-hole PCB pattern
vent_offset_deg  = 15;
feet_offset_deg  = 30;   // feet only
base_mount_offset_deg = 45; // base-mount holes only

// Feet
foot_count       = 0;
foot_height      = 5;
foot_d           = 8.0;
foot_edge_margin = 4;

// Cable notch (fixed orientation)
add_cable_notch  = true;
cable_notch_w    = 22;
cable_notch_d    = 6;

// ====== BASE MOUNTS (for fastening this base to something below) ======
add_base_mounts          = false;
base_mount_use_feet_radius = true;   // true: same radius as feet, false: use circle below
base_standoff_count      = 4;        // number of base-mount holes
base_standoff_circle_d   = 50;       // used when base_mount_use_feet_radius = false
base_hole_d              = 3.2;

use_base_countersink     = false;    // or true for 90° flat-heads
base_cbore_d             = 6.6;
base_cbore_depth_top     = 2.4;
base_csink_d_top         = 6.0;
base_csink_angle         = 90;

$fn = 128;

// ====== SAFETY GUARDS ======
min_bottom          = 1.2;  // leave at least this much under the deepest pocket
pocket_depth_       = min(pocket_depth, plate_thickness - min_bottom);
cbore_depth_        = min(cbore_depth, plate_thickness - 0.6);
base_cbore_depth_   = min(base_cbore_depth_top, plate_thickness - min_bottom);

// ====== HELPERS ======
module holes_on_circle(n, d_circle, hole_d, h, rot_deg=0) {
  rotate([0,0,rot_deg]) for (i=[0:n-1]) {
    a = 360/n * i;
    translate([ (d_circle/2)*cos(a), (d_circle/2)*sin(a), -0.1 ])
      cylinder(d=hole_d, h=h+0.2, $fn=48);
  }
}

module counterbores_on_circle(n, d_circle, bore_d, bore_h, z0, rot_deg=0) {
  if (bore_d>0 && bore_h>0)
  rotate([0,0,rot_deg]) for (i=[0:n-1]) {
    a = 360/n * i;
    translate([ (d_circle/2)*cos(a), (d_circle/2)*sin(a), z0 ])
      cylinder(d=bore_d, h=bore_h+0.2, $fn=64);
  }
}

module countersinks_on_circle(n, d_circle, d_top, angle_deg, z0, h_max, rot_deg=0) {
  // Approximate a countersink with a cone (linear_extrude of a circle via scale)
  rotate([0,0,rot_deg]) for (i=[0:n-1]) {
    a = 360/n * i;
    // height from top surface down to where the cone meets the shank
    h = min(h_max, plate_thickness - min_bottom);
    translate([ (d_circle/2)*cos(a), (d_circle/2)*sin(a), z0 ])
      cylinder(h=h+0.2, d1=d_top, d2=max(0.1, d_top - 2*h*tan((angle_deg/2)*PI/180)), $fn=64);
  }
}

function foot_radial(r_plate, r_foot) = r_plate - foot_edge_margin - r_foot;

module cable_notch() {
  if (add_cable_notch)
    translate([ (plate_outer_d/2) - cable_notch_d, -cable_notch_w/2, -0.1 ])
      cube([ cable_notch_d+0.2, cable_notch_w, plate_thickness+0.2 ]);
}

module feet(rot_deg=0) {
  r_plate = plate_outer_d/2;
  r_foot  = max(foot_d/2, 0.1);
  radial  = foot_radial(r_plate, r_foot);
  rotate([0,0,rot_deg]) for (i=[0:foot_count-1]) {
    a = 360/foot_count * i;
    translate([ radial*cos(a), radial*sin(a), -foot_height ])
      cylinder(d=foot_d, h=foot_height, $fn=64);
  }
}

// --- put this UP TOP with your other helpers ---
module sink_at(x, y) {
  // depth limited to keep bottom skin >= min_bottom
  cs_depth = min(plate_thickness - min_bottom, plate_thickness - 0.6);
  // bottom diameter from top diameter and angle
  d_bot = max(0.1, base_csink_d_top - 2*cs_depth*tan((base_csink_angle/2)*PI/180));
  translate([x, y, plate_thickness - cs_depth])
    cylinder(h = cs_depth + 0.2, d1 = base_csink_d_top, d2 = d_bot, $fn=64);
}

// Replace your base_mounts() with this:
module base_mounts() {
  if (add_base_mounts) {

    r_plate = plate_outer_d/2;
    r_foot  = max(foot_d/2, 0.1);
    radial_feet = foot_radial(r_plate, r_foot);

    if (base_mounts_follow_feet) {
      rotate([0,0,feet_offset_deg]) {
        for (i=[0:foot_count-1]) {
          a   = 360/foot_count * i;
          x   = radial_feet*cos(a);
          y   = radial_feet*sin(a);

          // through-hole
          translate([x, y, -0.1])
            cylinder(d = base_hole_d,
                     h = plate_thickness + foot_height + 0.3, $fn=48);

          // head treatment (entirely inside the thicker plate)
          if (use_base_countersink) {
            sink_at(x, y);
          } else {
            translate([x, y, plate_thickness - base_cbore_depth_])
              cylinder(d = base_cbore_d, h = base_cbore_depth_ + 0.2, $fn=64);
          }
        }
      }
    } else {
      // Independent circle for base mounts
      holes_on_circle(base_standoff_count, base_standoff_circle_d,
                      base_hole_d, plate_thickness + foot_height, base_mount_offset_deg);

      if (use_base_countersink) {
        // place individual sinks at the same circle
        rotate([0,0,base_mount_offset_deg]) {
          for (i=[0:base_standoff_count-1]) {
            a = 360/base_standoff_count * i;
            x = (base_standoff_circle_d/2)*cos(a);
            y = (base_standoff_circle_d/2)*sin(a);
            sink_at(x, y);
          }
        }
      } else {
        counterbores_on_circle(base_standoff_count, base_standoff_circle_d,
          base_cbore_d, base_cbore_depth_,
          plate_thickness - base_cbore_depth_, base_mount_offset_deg);
      }
    }
  }
}

// ====== MODEL ======
module plate_inset() {
  difference() {
    // main disk
    cylinder(d=plate_outer_d, h=plate_thickness);

    // PCB recess pocket
    translate([0,0,plate_thickness - pocket_depth_])
      cylinder(d=pocket_d, h=pocket_depth_ + 0.2);

    // 3-hole board mounts + counterbores
    holes_on_circle(3, bolt_circle_d, hole_clearance, plate_thickness, mount_offset_deg);
    for (i=[0:4]) {
      holes_on_circle(
        vent_holes,
        vent_circle_d,
        hole_clearance,
        plate_thickness,
        vent_offset_deg
      );
      vent_circle_d = vent_circle_d - 100;  
      vent_holes = vent_holes - 1;
    }
    counterbores_on_circle(3, bolt_circle_d, cbore_d, cbore_depth_,
                           plate_thickness - cbore_depth_, mount_offset_deg);

    // Base mounts (through + recessed heads)
    base_mounts();

    // Cable notch
    cable_notch();
  }
}

// Assemble
union() {
  if (foot_count) {
    feet(feet_offset_deg);
  }
  plate_inset();
}
