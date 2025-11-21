//
// Boompods Zero – Cup Holder (Frustum interior + USB port raised 5mm)
//

// -------------------------
// Speaker shape
// -------------------------
speaker_d_bottom = 31.7;    // diameter at the base of the speaker
speaker_d_mid    = 40.7;    // diameter halfway up (max width)
cup_height       = 22;      // wall height (~half the speaker height)

// Clearance for easy insertion/removal
clearance        = 0.7;

// Wall + base
wall_thickness   = 2.0;
base_thickness   = 2.0;

// Base plate dimensions
base_radius_extra = 4;
base_tab_y_extra  = 8;

// USB port window
port_width  = 16;
port_height = 10;   // thickness of opening
port_bottom_offset = 5;  // NEW: hole starts 5mm above the inner floor

// -------------------------

module boompod_cup() {

    // Inner radii (speaker shape)
    inner_r_bottom = speaker_d_bottom/2 + clearance;
    inner_r_top    = speaker_d_mid/2    + clearance;

    // Outer radii (constant wall thickness)
    outer_r_bottom = inner_r_bottom + wall_thickness;
    outer_r_top    = inner_r_top    + wall_thickness;

    // Base disk radius
    base_r = outer_r_bottom + base_radius_extra;

    difference() {
        union() {

            //-----------------------------
            // Base plate: circle + front tab
            //-----------------------------
            union() {
                cylinder(h = base_thickness, r = base_r, $fn=128);

                translate([0, base_r, 0])
                    cylinder(h = base_thickness, r = base_tab_y_extra, $fn=96);
            }

            //-----------------------------
            // Cup walls (frustum outer + frustum inner)
            //-----------------------------
            translate([0,0,base_thickness])
            difference() {

                // Outer frustum
                cylinder(
                    h = cup_height,
                    r1 = outer_r_bottom,
                    r2 = outer_r_top,
                    $fn=128
                );

                // Hollow interior frustum
                translate([0,0,wall_thickness])
                cylinder(
                    h = cup_height - wall_thickness,
                    r1 = inner_r_bottom,
                    r2 = inner_r_top,
                    $fn=128
                );
            }
        }

        //-----------------------------
        // USB cutout – raised 5mm above inner floor
        //-----------------------------
        translate([
            0,
            outer_r_top - wall_thickness/2,         // centered inside wall thickness
            base_thickness + port_bottom_offset + port_height/2
        ])
            cube([
                port_width,
                outer_r_top + 6,                    // extends fully through wall
                port_height
            ], center=true);
    }
}

boompod_cup();
